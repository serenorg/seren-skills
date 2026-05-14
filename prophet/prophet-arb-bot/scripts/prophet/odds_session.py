"""Poll Prophet's `oddsCalculationSession(id)` until terminal (issue #548).

Prophet's `/create` flow runs a 6-model AI odds calc (60–180s) after
`startOddsCalculation` returns a session id. The session's status enum
is `PENDING | CALCULATING | COMPLETED | FAILED | REJECTED`, and only
when it reaches `COMPLETED` does `pricing.yesFairValueBps` carry the
AI-computed fair value the seed-side decision needs.

This module is a thin GraphQL query loop:

  - `poll_odds_session(...)` issues `Query.oddsCalculationSession(id)`
    on a fixed interval until status is terminal (one of the three
    terminal values) or the overall timeout elapses.
  - Sleep + clock are injectable so tests run deterministically.
  - Transport-layer exceptions (`ProphetUnauthorized`,
    `ProphetGraphQLError`) propagate untouched — the runner already
    knows how to surface those as blocked-envelope reasons.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol


TERMINAL_STATUSES = {"COMPLETED", "FAILED", "REJECTED"}


class OddsSessionTimeout(Exception):
    """Polling exceeded `timeout_s` before status became terminal."""


class _Transport(Protocol):
    def post_graphql(
        self,
        *,
        jwt: str | None,
        query: str,
        variables: dict[str, Any] | None = None,
        operation_name: str | None = None,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class BinaryPricing:
    """Prophet `BinaryPricing` projection — only the fields the seed-side
    decision and viability gate actually read."""

    yes_price_bps: int
    no_price_bps: int
    yes_fair_value_bps: int
    no_fair_value_bps: int
    is_viable: bool
    confidence_bps: int

    @classmethod
    def from_dict(cls, payload: Any) -> "BinaryPricing | None":
        if not isinstance(payload, dict):
            return None
        try:
            return cls(
                yes_price_bps=int(payload["yesPriceBps"]),
                no_price_bps=int(payload["noPriceBps"]),
                yes_fair_value_bps=int(payload["yesFairValueBps"]),
                no_fair_value_bps=int(payload["noFairValueBps"]),
                is_viable=bool(payload["isViable"]),
                confidence_bps=int(payload.get("confidenceBps", 0)),
            )
        except (KeyError, TypeError, ValueError):
            return None


@dataclass(frozen=True)
class OddsSession:
    """`OddsCalculationSession` terminal snapshot."""

    id: str
    status: str
    total_models: int
    completed_models: int
    pricing: BinaryPricing | None
    rejection_reason: str | None


_QUERY = """
query OddsCalculationSession($id: ID!) {
  oddsCalculationSession(id: $id) {
    id
    status
    totalModels
    completedModels
    pricing {
      yesPriceBps
      noPriceBps
      yesFairValueBps
      noFairValueBps
      isViable
      confidenceBps
    }
    rejectionReason
  }
}
""".strip()


def poll_odds_session(
    transport: _Transport,
    *,
    jwt: str,
    session_id: str,
    interval_s: float = 2.0,
    timeout_s: float = 180.0,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> OddsSession:
    """Block until `oddsCalculationSession(id)` reports a terminal status.

    Raises:
      OddsSessionTimeout    if the wall-clock budget elapses first.
      ProphetUnauthorized   from the transport on a 401 (caller resets JWT).
      ProphetGraphQLError   from the transport on any other GraphQL fault.
    """
    deadline = now() + max(timeout_s, 0.0)
    while True:
        response = transport.post_graphql(
            jwt=jwt,
            query=_QUERY,
            variables={"id": session_id},
            operation_name="OddsCalculationSession",
        )
        node = (
            response.get("data", {}).get("oddsCalculationSession")
            if isinstance(response, dict)
            else None
        )
        if isinstance(node, dict):
            status = str(node.get("status", ""))
            if status in TERMINAL_STATUSES:
                return OddsSession(
                    id=str(node.get("id") or session_id),
                    status=status,
                    total_models=int(node.get("totalModels") or 0),
                    completed_models=int(node.get("completedModels") or 0),
                    pricing=BinaryPricing.from_dict(node.get("pricing")),
                    rejection_reason=node.get("rejectionReason"),
                )
        if now() >= deadline:
            raise OddsSessionTimeout(
                f"odds session {session_id!r} did not reach terminal status within {timeout_s}s"
            )
        if interval_s > 0:
            sleep(interval_s)
