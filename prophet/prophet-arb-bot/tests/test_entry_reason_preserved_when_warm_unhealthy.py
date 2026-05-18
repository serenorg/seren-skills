"""Issue #672: the warm-context health check must not clobber sub.reason.

Pre-#672 the per-entry create-market loop unconditionally overwrote sub
with `CycleResult(reason="warm_context_corrupted")` whenever the warm
Playwright context lost observable Prophet auth — erasing every real
upstream reason from the inner driver (ok/pair_created, no_edge,
ocs_session_id_not_captured, hedge_failed_no_commit, exception captures
under create_market_via_ui_unexpected with `payload.error`, etc.).

After the fix the inner sub is preserved verbatim, with
`payload.warm_unhealthy_post_entry=True` annotated as a supplemental
signal. The reopen behavior is unchanged — only the reason-clobber is
removed.
"""

from __future__ import annotations

import agent  # noqa: E402  (PYTHONPATH=scripts)
from agent import CycleResult, _annotate_entry_result_with_warm_health


def test_warm_unhealthy_preserves_inner_reason_and_annotates_payload():
    # 1. Blocked entry from inner driver: real reason must survive.
    blocked = CycleResult(
        status="blocked",
        reason="ocs_session_id_not_captured",
        payload={"polymarket_condition_id": "0xabc"},
    )
    annotated = _annotate_entry_result_with_warm_health(blocked, warm_unhealthy=True)
    assert annotated.status == "blocked"
    assert annotated.reason == "ocs_session_id_not_captured"
    assert annotated.reason != "warm_context_corrupted"
    assert annotated.payload["polymarket_condition_id"] == "0xabc"
    assert annotated.payload["warm_unhealthy_post_entry"] is True

    # 2. Successful entry: success must survive even if warm unhealthy.
    ok = CycleResult(
        status="ok",
        reason="pair_created",
        payload={"prophet_market_id": "mkt_xyz"},
    )
    annotated_ok = _annotate_entry_result_with_warm_health(ok, warm_unhealthy=True)
    assert annotated_ok.status == "ok"
    assert annotated_ok.reason == "pair_created"
    assert annotated_ok.payload["prophet_market_id"] == "mkt_xyz"
    assert annotated_ok.payload["warm_unhealthy_post_entry"] is True

    # 3. Exception-capture entry: payload.error must survive.
    crashed = CycleResult(
        status="blocked",
        reason="create_market_via_ui_unexpected",
        payload={"polymarket_condition_id": "0xdef", "error": "TimeoutError:..."},
    )
    annotated_crash = _annotate_entry_result_with_warm_health(
        crashed, warm_unhealthy=True
    )
    assert annotated_crash.reason == "create_market_via_ui_unexpected"
    assert annotated_crash.payload["error"] == "TimeoutError:..."
    assert annotated_crash.payload["warm_unhealthy_post_entry"] is True

    # 4. Healthy warm context: passthrough — no payload mutation.
    untouched = _annotate_entry_result_with_warm_health(ok, warm_unhealthy=False)
    assert untouched is ok
    assert "warm_unhealthy_post_entry" not in ok.payload
