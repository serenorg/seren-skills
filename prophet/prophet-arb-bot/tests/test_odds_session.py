"""Critical-path tests for prophet/odds_session.py (issue #548).

Covers exactly the behaviors that gate the per-market seed-side decision:

  1. Returning a parsed `OddsSession` on a `COMPLETED` response (the happy
     path the agent reads `yesFairValueBps` from).
  2. Polling until the status flips to a terminal value (the runtime
     reason this helper exists at all — Prophet's 6-model calc takes
     60–120s).
  3. Surfacing `FAILED` / `REJECTED` cleanly so the agent can abandon the
     candidate without committing either leg.
  4. Raising `OddsSessionTimeout` when the calc never terminates.

Anything beyond these is duplicated coverage of transport-layer behavior
already pinned in `test_prophet_transport.py`.
"""

from __future__ import annotations

import pytest

from prophet.odds_session import (
    OddsSession,
    OddsSessionTimeout,
    poll_odds_session,
)


def _completed_payload(*, yes_fair_value_bps: int = 5800, is_viable: bool = True) -> dict:
    return {
        "data": {
            "oddsCalculationSession": {
                "id": "ocs_1",
                "status": "COMPLETED",
                "totalModels": 6,
                "completedModels": 6,
                "pricing": {
                    "yesPriceBps": 5700,
                    "noPriceBps": 4300,
                    "yesFairValueBps": yes_fair_value_bps,
                    "noFairValueBps": 10000 - yes_fair_value_bps,
                    "isViable": is_viable,
                    "confidenceBps": 7200,
                },
                "rejectionReason": None,
            }
        }
    }


def _calculating_payload(completed: int) -> dict:
    return {
        "data": {
            "oddsCalculationSession": {
                "id": "ocs_1",
                "status": "CALCULATING",
                "totalModels": 6,
                "completedModels": completed,
                "pricing": None,
                "rejectionReason": None,
            }
        }
    }


def _failed_payload() -> dict:
    return {
        "data": {
            "oddsCalculationSession": {
                "id": "ocs_1",
                "status": "FAILED",
                "totalModels": 6,
                "completedModels": 3,
                "pricing": None,
                "rejectionReason": "model_disagreement_exceeded_threshold",
            }
        }
    }


class ScriptedTransport:
    """Returns the i-th payload on the i-th call, then last forever."""

    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = payloads
        self.calls: list[dict] = []

    def post_graphql(self, *, jwt, query, variables=None, operation_name=None):
        self.calls.append(
            {"jwt": jwt, "variables": variables, "operation_name": operation_name}
        )
        idx = min(len(self.calls) - 1, len(self.payloads) - 1)
        return self.payloads[idx]


def test_returns_parsed_session_on_completed_first_response():
    transport = ScriptedTransport([_completed_payload(yes_fair_value_bps=5900)])
    session = poll_odds_session(
        transport,
        jwt="eyJ...",
        session_id="ocs_1",
        interval_s=0.0,
        timeout_s=1.0,
        sleep=lambda _s: None,
    )
    assert isinstance(session, OddsSession)
    assert session.status == "COMPLETED"
    assert session.pricing is not None
    assert session.pricing.yes_fair_value_bps == 5900
    assert session.pricing.is_viable is True
    assert len(transport.calls) == 1
    assert transport.calls[0]["variables"] == {"id": "ocs_1"}
    assert transport.calls[0]["jwt"] == "eyJ..."


def test_polls_until_status_becomes_terminal():
    transport = ScriptedTransport(
        [
            _calculating_payload(completed=2),
            _calculating_payload(completed=4),
            _completed_payload(yes_fair_value_bps=5500),
        ]
    )
    sleeps: list[float] = []
    session = poll_odds_session(
        transport,
        jwt="eyJ...",
        session_id="ocs_1",
        interval_s=0.5,
        timeout_s=10.0,
        sleep=sleeps.append,
    )
    assert session.status == "COMPLETED"
    assert session.pricing is not None
    assert session.pricing.yes_fair_value_bps == 5500
    assert len(transport.calls) == 3
    # Two sleeps between three polls.
    assert sleeps == [0.5, 0.5]


def test_failed_status_returns_session_with_no_pricing():
    transport = ScriptedTransport([_failed_payload()])
    session = poll_odds_session(
        transport,
        jwt="eyJ...",
        session_id="ocs_1",
        interval_s=0.0,
        timeout_s=1.0,
        sleep=lambda _s: None,
    )
    assert session.status == "FAILED"
    assert session.pricing is None
    assert session.rejection_reason == "model_disagreement_exceeded_threshold"


def test_timeout_raises_when_never_terminal():
    transport = ScriptedTransport([_calculating_payload(completed=1)])
    # Fake monotonic clock that advances 0.6s per call so 1.0s timeout
    # fires on the second observation.
    ticks = iter([0.0, 0.0, 0.6, 1.2, 1.8])

    def fake_now() -> float:
        return next(ticks)

    with pytest.raises(OddsSessionTimeout):
        poll_odds_session(
            transport,
            jwt="eyJ...",
            session_id="ocs_1",
            interval_s=0.0,
            timeout_s=1.0,
            sleep=lambda _s: None,
            now=fake_now,
        )
