"""MinimalProphetClient — operation-specific helpers around prophet-ai.

Only the operations this skill actually makes are exposed:

  - viewer()                          — JWT validation + identity binding
  - markets_for_dedup(...)            — public read used by §14.3 dedup
  - market(market_id)                 — single-market re-fetch for the
                                        post-create eligibility gates
                                        (resolutionDate < deadline,
                                         creator.id matches viewer.id)
  - create_market_chain(...)          — the four-step write chain:
                                          initiateMarket
                                          → startOddsCalculation
                                          → oddsCalculationSession (poll)
                                          → marketCreationOrderParams
                                          → createMarketWithBet

Field-name validation: the exact field shapes for `Market.creator` and
the create-chain inputs are confirmed via `schema_probe.py` against the
live publisher; the client uses placeholders documented from plan §12.1
and §16.1 that must match the captured fixture before Phase 14 acceptance.

The client takes a `gateway` object with a `call(publisher, method,
path, body, headers)` signature. The agent's gateway is the production
implementation; tests inject a stub.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from . import (
    ProphetGraphQLError,
    ProphetSchemaError,
    ProphetUnauthorized,
)

PUBLISHER = "prophet-ai"
GRAPHQL_PATH = "/api/graphql"

# Bounty deadline that markets must resolve before to be eligible.
# Plan §3 ADR + §16.1 post-create gate.
BOUNTY_RESOLUTION_DEADLINE_ISO = "2026-05-11T00:00:00Z"


@dataclass
class ViewerIdentity:
    id: str
    email: str


@dataclass
class ProphetMarketRef:
    """Subset of fields the post-create gate inspects.

    The full Market type has many more fields (slug, betting config,
    odds, etc.); we only surface what the bounty-eligibility check needs.
    """

    market_id: str
    slug: str
    resolution_date: str
    creator_viewer_id: str
    url: str = ""


class MinimalProphetClient:
    def __init__(self, *, gateway: Any) -> None:
        self.gateway = gateway

    # ------------------------------------------------------------------
    # Authenticated reads — require Privy JWT passthrough

    def viewer(self, *, jwt: str) -> ViewerIdentity:
        """`Query: viewer { id email }` — JWT validation + identity binding.

        Plan §11.1 step 10, §17 schema. Called once after OTP acquisition;
        the returned `id` becomes `participant_identity.prophet_viewer_id`
        and is the per-user attribution key the operator's reconciler
        relies on (§3 ADR P0).
        """
        payload = self._post(
            jwt=jwt,
            query="query Viewer { viewer { id email } }",
            variables={},
        )
        viewer = ((payload or {}).get("data") or {}).get("viewer") or {}
        viewer_id = viewer.get("id") or ""
        viewer_email = viewer.get("email") or ""
        if not viewer_id or not viewer_email:
            raise ProphetSchemaError(
                f"viewer query returned incomplete payload: id={viewer_id!r} email={viewer_email!r}"
            )
        return ViewerIdentity(id=viewer_id, email=viewer_email)

    # ------------------------------------------------------------------
    # Public reads — no JWT required, but we still pass it when present

    def markets_for_dedup(
        self, *, jwt: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        """List currently-listed Prophet markets for the dedup pre-filter.

        Plan §14.3 mandates this before any candidate is submitted; if
        the publisher is unavailable the run blocks rather than fails open.
        Returns the raw `markets` array; callers normalize question/slug.
        """
        query = """
        query MarketsForDedup($input: MarketsInput) {
          markets(input: $input) {
            id
            slug
            question
            resolutionDate
          }
        }
        """
        payload = self._post(
            jwt=jwt,
            query=query,
            variables={"input": {"limit": limit, "status": "active"}},
        )
        markets = ((payload or {}).get("data") or {}).get("markets")
        if not isinstance(markets, list):
            raise ProphetSchemaError(
                "markets query did not return a list — schema may have drifted"
            )
        return markets

    def market(self, *, jwt: str, market_id: str) -> ProphetMarketRef:
        """Re-fetch a single Prophet market after createMarketWithBet.

        Plan §16.1: enforces three eligibility gates:
          1. The market actually exists.
          2. resolutionDate < BOUNTY_RESOLUTION_DEADLINE_ISO.
          3. creator.id == participant_identity.prophet_viewer_id.

        Failures route to events with the specific event_type names
        documented in plan §17.2 (`prophet.market_resolution_date_ineligible`,
        `prophet.market_creator_mismatch`); raising here lets the caller
        record the right type.
        """
        query = """
        query MarketById($id: ID!) {
          market(id: $id) {
            id
            slug
            url
            resolutionDate
            creator {
              id
            }
          }
        }
        """
        payload = self._post(jwt=jwt, query=query, variables={"id": market_id})
        market = ((payload or {}).get("data") or {}).get("market") or {}
        if not market.get("id"):
            raise ProphetSchemaError(
                f"market({market_id}) returned no record — does not exist or schema drift"
            )
        creator = market.get("creator") or {}
        return ProphetMarketRef(
            market_id=market.get("id") or "",
            slug=market.get("slug") or "",
            resolution_date=market.get("resolutionDate") or "",
            creator_viewer_id=creator.get("id") or "",
            url=market.get("url") or "",
        )

    # ------------------------------------------------------------------
    # Authenticated writes — the four-step market creation chain

    def create_market_chain(
        self,
        *,
        jwt: str,
        question: str,
        category_slug: str,
        topic_slug: str,
        resolution_date_iso: str,
        initial_bet_usdc: int,
        poll_interval_seconds: float = 1.5,
        poll_timeout_seconds: float = 30.0,
    ) -> str:
        """Run initiateMarket → odds calculation → createMarketWithBet.

        Returns the new prophet_market_id on success.

        Plan §12.1: "Confirm the exact input shapes by introspecting the
        live schema via the prophet-ai publisher before writing the
        calls." The variable shapes here are placeholders derived from
        the publisher's documented use_cases; schema_probe.py captures
        the authoritative shapes during Phase 14 acceptance.
        """
        # Step 1: initiateMarket — server-side draft.
        init_payload = self._post(
            jwt=jwt,
            query="""
            mutation InitiateMarket($input: InitiateMarketInput!) {
              initiateMarket(input: $input) {
                draftId
              }
            }
            """,
            variables={
                "input": {
                    "question": question,
                    "categorySlug": category_slug,
                    "topicSlug": topic_slug,
                    "resolutionDate": resolution_date_iso,
                }
            },
        )
        draft_id = (
            ((init_payload or {}).get("data") or {})
            .get("initiateMarket", {})
            .get("draftId")
        )
        if not draft_id:
            raise ProphetSchemaError("initiateMarket did not return draftId")

        # Step 2: startOddsCalculation — async session.
        start_payload = self._post(
            jwt=jwt,
            query="""
            mutation StartOddsCalculation($draftId: ID!) {
              startOddsCalculation(draftId: $draftId) {
                sessionId
              }
            }
            """,
            variables={"draftId": draft_id},
        )
        session_id = (
            ((start_payload or {}).get("data") or {})
            .get("startOddsCalculation", {})
            .get("sessionId")
        )
        if not session_id:
            raise ProphetSchemaError("startOddsCalculation did not return sessionId")

        # Step 3: poll oddsCalculationSession until status == 'COMPLETED'.
        odds_ready_at = time.monotonic() + poll_timeout_seconds
        odds: dict[str, Any] = {}
        while time.monotonic() < odds_ready_at:
            poll = self._post(
                jwt=jwt,
                query="""
                query OddsCalculationSession($id: ID!) {
                  oddsCalculationSession(id: $id) {
                    status
                    odds
                  }
                }
                """,
                variables={"id": session_id},
            )
            session = (
                ((poll or {}).get("data") or {}).get("oddsCalculationSession") or {}
            )
            status = session.get("status") or ""
            if status.upper() == "COMPLETED":
                odds = session.get("odds") or {}
                break
            if status.upper() in {"FAILED", "ERROR"}:
                raise ProphetGraphQLError(f"odds calculation failed: status={status!r}")
            time.sleep(poll_interval_seconds)
        else:
            raise ProphetGraphQLError(
                f"odds calculation timed out after {poll_timeout_seconds:.0f}s"
            )

        # Step 4: marketCreationOrderParams + createMarketWithBet.
        params_payload = self._post(
            jwt=jwt,
            query="""
            query MarketCreationOrderParams($draftId: ID!, $betUsdc: Int!) {
              marketCreationOrderParams(draftId: $draftId, betUsdc: $betUsdc) {
                params
              }
            }
            """,
            variables={"draftId": draft_id, "betUsdc": initial_bet_usdc},
        )
        order_params = (
            ((params_payload or {}).get("data") or {})
            .get("marketCreationOrderParams", {})
            .get("params")
        )
        if order_params is None:
            raise ProphetSchemaError("marketCreationOrderParams did not return params")

        create_payload = self._post(
            jwt=jwt,
            query="""
            mutation CreateMarketWithBet($input: CreateMarketWithBetInput!) {
              createMarketWithBet(input: $input) {
                market {
                  id
                }
              }
            }
            """,
            variables={
                "input": {
                    "draftId": draft_id,
                    "orderParams": order_params,
                    "odds": odds,
                }
            },
        )
        market = (
            ((create_payload or {}).get("data") or {})
            .get("createMarketWithBet", {})
            .get("market")
            or {}
        )
        market_id = market.get("id") or ""
        if not market_id:
            raise ProphetSchemaError("createMarketWithBet did not return a market.id")
        return market_id

    # ------------------------------------------------------------------
    # transport — uniform error handling

    def _post(
        self, *, jwt: str | None, query: str, variables: dict[str, Any]
    ) -> dict[str, Any]:
        headers: dict[str, str] = {}
        if jwt:
            headers["Authorization"] = f"Bearer {jwt}"

        try:
            response = self.gateway.call(
                PUBLISHER,
                "POST",
                GRAPHQL_PATH,
                body={"query": query, "variables": variables},
                headers=headers,
            )
        except _StatusError as exc:
            if exc.status == 401:
                raise ProphetUnauthorized("prophet-ai returned 401") from exc
            raise

        # The gateway should already raise on non-2xx; if a stub or
        # connector chooses to surface them as response payloads, treat
        # them as auth failures or schema errors.
        if isinstance(response, dict) and response.get("status") == 401:
            raise ProphetUnauthorized("prophet-ai returned 401")

        if not isinstance(response, dict):
            raise ProphetSchemaError(
                f"prophet-ai returned non-dict payload: {type(response).__name__}"
            )

        errors = response.get("errors")
        if errors:
            # GraphQL errors. Surface the first message; tests assert on
            # the exception type, not the message body.
            first = errors[0] if isinstance(errors, list) and errors else {}
            message = (
                first.get("message") if isinstance(first, dict) else str(first)
            ) or "unknown GraphQL error"
            raise ProphetGraphQLError(f"prophet-ai GraphQL errors: {message}")

        return response


class _StatusError(Exception):
    """Internal sentinel; gateways may raise their own error types.

    Tests stub the gateway directly and can raise this to simulate 401.
    """

    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.status = status
