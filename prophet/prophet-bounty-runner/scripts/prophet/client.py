"""MinimalProphetClient — operation-specific helpers for Prophet GraphQL.

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
the create-chain inputs are confirmed via `schema_probe.py` against
Prophet's live API; the client uses placeholders documented from plan
§12.1 and §16.1 that must match the captured fixture before Phase 14
acceptance.

Calls go directly to `https://app.prophetmarket.ai/api/graphql` via the
injected `ProphetTransport` (see `transport.py`). The earlier
`prophet-ai` Seren publisher hop was removed in #493 because Prophet's
auth (Authorization: Bearer JWT) collided with the gateway's
SEREN_API_KEY slot. Tests inject a StubProphetTransport.
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

# Bounty deadline that markets must resolve before to be eligible.
# Plan §3 ADR + §16.1 post-create gate.
# Phase 15 (#505): tightened to 2026-05-24 to match Prophet's `/create`
# UI window. Must agree with `agent.BOUNTY_DEADLINE_ISO`; the drift
# guard in tests/test_bounty_deadline_consistency.py fails the CI if
# the two ever diverge.
BOUNTY_RESOLUTION_DEADLINE_ISO = "2026-05-24T00:00:00Z"


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
    def __init__(self, *, transport: Any) -> None:
        """Wrap a Prophet HTTP transport with operation-specific helpers.

        `transport` must expose `post_graphql(*, jwt, query, variables,
        operation_name=None) -> dict` and raise `ProphetUnauthorized` on
        401 / `ProphetGraphQLError` on other transport or GraphQL
        failures. The production implementation is
        `prophet.transport.ProphetDirectTransport`; tests pass
        `StubProphetTransport` from conftest.
        """
        self.transport = transport

    # ------------------------------------------------------------------
    # Authenticated reads — require Privy JWT passthrough

    def viewer(self, *, jwt: str) -> ViewerIdentity:
        """`Query: viewer { user { id email } }` — JWT validation + identity binding.

        Plan §11.1 step 10, §17 schema. Called once after OTP acquisition;
        the returned `id` becomes `participant_identity.prophet_viewer_id`
        and is the per-user attribution key the operator's reconciler
        relies on (§3 ADR P0).

        Issue #502: Prophet's live `Viewer` type does not expose `id` or
        `email` directly — those fields moved under `Viewer.user`. The
        legacy flat shape `viewer { id email }` is rejected with
        `GRAPHQL_VALIDATION_FAILED` and surfaces as an opaque
        "An unexpected error occurred" mask in the client response.
        """
        payload = self._post(
            jwt=jwt,
            query="query Viewer { viewer { user { id email } } }",
            variables={},
            operation_name="Viewer",
        )
        viewer = ((payload or {}).get("data") or {}).get("viewer") or {}
        user = viewer.get("user") or {}
        viewer_id = user.get("id") or ""
        viewer_email = user.get("email") or ""
        if not viewer_id or not viewer_email:
            raise ProphetSchemaError(
                f"viewer query returned incomplete payload: id={viewer_id!r} email={viewer_email!r}"
            )
        return ViewerIdentity(id=viewer_id, email=viewer_email)

    # ------------------------------------------------------------------
    # Public reads — no JWT required, but we still pass it when present

    def markets_for_dedup(
        self,
        *,
        jwt: str | None = None,
        first: int = 200,
        resolving_before_iso: str | None = None,
    ) -> list[dict[str, Any]]:
        """List currently-listed Prophet markets for the dedup pre-filter.

        Plan §14.3 mandates this before any candidate is submitted; if
        the publisher is unavailable the run blocks rather than fails open.

        Query shape (pinned against `tests/fixtures/prophet_schema.json`,
        captured live 2026-05-13 — see #505 Phase 14b):

            Query.markets(input: MarketsInput): MarketConnection!
            input MarketsInput {
              first: Int, after: String, last: Int, before: String,
              filter: MarketFilter, sort: MarketSort
            }
            input MarketFilter {
              status: MarketStatus, creatorId: ID, search: String,
              resolvingBefore: Time, resolvingAfter: Time,
              categorySlug: String, topicSlug: String
            }
            type MarketConnection {
              edges: [MarketEdge!]!, pageInfo: PageInfo!, totalCount: Int!
            }
            type MarketEdge { node: Market!, cursor: String! }

        Returns the flattened list of `node` records so callers can read
        `question` / `slug` / `resolutionDate` without re-walking the
        connection envelope.
        """
        query = """
        query MarketsForDedup($input: MarketsInput) {
          markets(input: $input) {
            edges {
              node {
                id
                slug
                question
                resolutionDate
              }
            }
          }
        }
        """
        filter_: dict[str, Any] = {"status": "ACTIVE"}
        if resolving_before_iso:
            filter_["resolvingBefore"] = resolving_before_iso
        payload = self._post(
            jwt=jwt,
            query=query,
            variables={"input": {"first": first, "filter": filter_}},
            operation_name="MarketsForDedup",
        )
        connection = ((payload or {}).get("data") or {}).get("markets") or {}
        edges = connection.get("edges")
        if not isinstance(edges, list):
            raise ProphetSchemaError(
                "markets query did not return a connection — schema may have drifted"
            )
        return [
            edge.get("node") or {}
            for edge in edges
            if isinstance(edge, dict)
        ]

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
        payload = self._post(
            jwt=jwt,
            query=query,
            variables={"id": market_id},
            operation_name="MarketById",
        )
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
            operation_name="InitiateMarket",
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
            operation_name="StartOddsCalculation",
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
                operation_name="OddsCalculationSession",
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
            operation_name="MarketCreationOrderParams",
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
            operation_name="CreateMarketWithBet",
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
    # transport delegation

    def _post(
        self,
        *,
        jwt: str | None,
        query: str,
        variables: dict[str, Any],
        operation_name: str | None = None,
    ) -> dict[str, Any]:
        """Single seam through which every authenticated Prophet call
        flows. The transport handles HTTP, 401 → ProphetUnauthorized,
        and `errors[]` → ProphetGraphQLError. Anything that comes back
        here must already be a dict with a populated `data` key.

        `operation_name` is forwarded as the GraphQL `operationName`
        field so that Prophet's request logs (and test stubs) can
        attribute calls to the specific chain step that issued them.
        Callers pass the exact name they declared in the query body.
        """
        response = self.transport.post_graphql(
            jwt=jwt,
            query=query,
            variables=variables,
            operation_name=operation_name,
        )
        if not isinstance(response, dict):
            raise ProphetSchemaError(
                f"prophet returned non-dict payload: {type(response).__name__}"
            )
        return response
