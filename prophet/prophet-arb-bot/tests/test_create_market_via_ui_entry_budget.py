"""Issue #652: per-entry wall-clock budget for the /create UI driver.

Replaces per-`tools/call` timeout policing. The budget is the single cap
on a `/create` entry; per-call ceilings exist only to detect a dead MCP
stdio stream (gateway floor pinned at 180s; see
test_playwright_mcp_cold_launch.py).

Only the two abort paths that protect against naked Polymarket exposure
are pinned here:

  1. Budget exceeded before the Polymarket hedge has committed →
     blocked + no record_created_market call.
  2. Budget exceeded after the hedge has committed →
     record_created_market is invoked with prophet_confirm_declined=True
     to unwind the leg before returning blocked.

Both tests inject a fake monotonic clock and a small budget so the trip
is deterministic without sleeping.
"""

from __future__ import annotations

from typing import Any

from agent import AgentConfig, CycleResult, cmd_create_market_via_ui
from arbitrage.intelligence import IntelligenceConfig
from arbitrage.scoring import ScoringConfig
from discovery import AutoDiscoverConfig


def _config(budget_seconds: float) -> AgentConfig:
    return AgentConfig(
        inputs={"prophet_email": "jill@volume.finance", "email_provider": "gmail"},
        project_name="prophet",
        database_name="prophet",
        scoring=ScoringConfig(),
        intelligence=IntelligenceConfig(),
        auto_discover=AutoDiscoverConfig(
            enabled=True,
            initial_bet_usdc=1.0,
            create_market_entry_budget_seconds=budget_seconds,
        ),
        live_mode=True,
        max_orders_per_run=5,
        execution_mode="delta_neutral",
        max_hedge_slippage_bps=100.0,
    )


class _FreshCacheEntry:
    jwt = "eyJ.fresh.jwt"
    refresh_token = "rt_fresh"
    prophet_viewer_id = "vid_x"
    state = "fresh"

    def is_fresh(self, *, leeway_seconds: int = 60) -> bool:
        return True


def _stub_establish_session(**_kwargs: Any) -> _FreshCacheEntry:
    return _FreshCacheEntry()


class _StubSession:
    def navigate(self, url: str) -> None: ...
    def click(self, selector: str) -> None: ...
    def fill(self, selector: str, value: str) -> None: ...
    def wait_for(self, selector: str, *, timeout_ms: int = 30_000) -> None: ...
    def get_url(self) -> str:
        return ""
    def evaluate(self, script: str) -> Any:
        return ""


class _SessionScope:
    def __init__(self, session: _StubSession) -> None:
        self._s = session

    def __enter__(self) -> _StubSession:
        return self._s

    def __exit__(self, *args: Any) -> None:
        return None


class _StubCreateMarketUI:
    """No-op UI helpers — every call is fast."""

    def __init__(self, ocs_id: str = "ocs_test") -> None:
        self.ocs_id = ocs_id
        self.calls: list[str] = []

    def open_create_form(self, session: Any, *, question: str) -> None:
        self.calls.append("open_create_form")

    def poll_for_ocs_id(self, session: Any, **kwargs: Any) -> str:
        self.calls.append("poll_for_ocs_id")
        return self.ocs_id

    def fill_bet_form(self, session: Any, *, seed_side: str, bet_usdc: float) -> None:
        self.calls.append("fill_bet_form")

    def click_prophet_confirm(self, session: Any) -> None:
        self.calls.append("click_prophet_confirm")

    def wait_for_market_redirect(self, session: Any, **kwargs: Any) -> str:
        self.calls.append("wait_for_market_redirect")
        # Should never reach here in either budget-exceeded test.
        return "m_should_not_redirect"


def _seed_intent_ok(**kwargs: Any) -> CycleResult:
    return CycleResult(
        status="ok",
        reason="seed_intent_ready",
        payload={
            "polymarket_condition_id": kwargs["polymarket_condition_id"],
            "seed_side": "buy",
            "hedge_side": "sell",
            "hedge_price": 0.42,
            "tick_size": "0.01",
            "edge_bps": 800.0,
            "session_id": "ocs_test",
            "session_status": "COMPLETED",
            "is_viable": True,
            "prophet_fair_value_bps": 5800,
            "polymarket_yes_price": 0.50,
            "polymarket_yes_price_source": "caller_supplied",
        },
    )


def test_entry_budget_exceeded_pre_hedge_blocks_without_calling_unwind() -> None:
    """Budget trips before the hedge commits → no record_created_market call."""
    record_calls: list[dict[str, Any]] = []

    def stub_record_created_market(**kwargs: Any) -> CycleResult:
        record_calls.append(dict(kwargs))
        return CycleResult(status="ok", reason="recorded", payload={"hedge_status": "hedged"})

    ui = _StubCreateMarketUI()
    # Clock returns 0 on the start_monotonic read, then 9999 on the first
    # budget check (well past the 1.0s budget). Trips at the earliest
    # budget gate, before fill_bet_form / record_created_market.
    clock_values = iter([0.0])
    fake_clock = lambda: next(clock_values, 9999.0)

    result = cmd_create_market_via_ui(
        config=_config(budget_seconds=1.0),
        gateway=object(),
        transport=object(),
        polymarket_condition_id="0xCID",
        question="Will X happen?",
        initial_bet_usdc=1.0,
        open_session_factory=lambda: _SessionScope(_StubSession()),
        establish_session=_stub_establish_session,
        create_market_ui=ui,
        compute_seed_intent=_seed_intent_ok,
        record_created_market=stub_record_created_market,
        now=fake_clock,
    )

    assert result.status == "blocked"
    assert result.reason == "create_market_via_ui_entry_budget_exceeded"
    # CRITICAL: no record_created_market call at all — no naked exposure
    # could have been created, so there is nothing to unwind.
    assert record_calls == [], (
        "pre-hedge budget trip must NOT invoke record_created_market; "
        f"saw {record_calls!r}"
    )
    # Confirm click never attempted.
    assert "click_prophet_confirm" not in ui.calls
    # Payload surfaces budget + elapsed + last stage for operator visibility.
    assert result.payload["entry_budget_seconds"] == 1.0
    assert result.payload["entry_elapsed_seconds"] > 1.0
    assert result.payload.get("entry_last_stage")


def test_entry_budget_exceeded_post_hedge_invokes_unwind_with_decline_flag() -> None:
    """Budget trips after the hedge commits → unwind invoked before blocking."""
    record_calls: list[dict[str, Any]] = []
    # Mutable flag the fake clock reads. Flipped True the moment the
    # hedge stub records the first non-declined call (i.e. the hedge
    # submit). After that, every clock read returns a value past the
    # budget — so the FIRST post-hedge budget check trips.
    post_hedge = [False]

    def stub_record_created_market(**kwargs: Any) -> CycleResult:
        record_calls.append(dict(kwargs))
        if not kwargs.get("prophet_confirm_declined"):
            post_hedge[0] = True
            return CycleResult(
                status="ok",
                reason="seed_hedge_ready_for_prophet_confirm",
                payload={
                    "hedge_status": "hedged",
                    "next_action": "click_prophet_confirm",
                    "polymarket_order_id": "po_1",
                },
            )
        return CycleResult(
            status="ok",
            reason="prophet_confirm_declined",
            payload={"hedge_status": "unwound_after_prophet_decline"},
        )

    ui = _StubCreateMarketUI()
    # Robust against future budget-check insertions: clock returns 1.0
    # while no hedge has committed, 9999.0 after — so pre-hedge checks
    # always pass and the very first post-hedge check trips, no matter
    # how many gates we add to the inner.
    def fake_clock() -> float:
        return 9999.0 if post_hedge[0] else 1.0

    result = cmd_create_market_via_ui(
        config=_config(budget_seconds=60.0),
        gateway=object(),
        transport=object(),
        polymarket_condition_id="0xCID",
        question="Will X happen?",
        initial_bet_usdc=1.0,
        open_session_factory=lambda: _SessionScope(_StubSession()),
        establish_session=_stub_establish_session,
        create_market_ui=ui,
        compute_seed_intent=_seed_intent_ok,
        record_created_market=stub_record_created_market,
        now=fake_clock,
    )

    assert result.status == "blocked"
    assert result.reason == "create_market_via_ui_entry_budget_exceeded"
    # CRITICAL: exactly two record_created_market calls — the hedge submit
    # and the unwind. The unwind must carry prophet_confirm_declined=True.
    assert len(record_calls) == 2, (
        f"expected hedge submit + unwind, got {len(record_calls)} calls: {record_calls!r}"
    )
    assert record_calls[0].get("prophet_confirm_declined") is False
    assert record_calls[1].get("prophet_confirm_declined") is True
    assert record_calls[1]["polymarket_condition_id"] == "0xCID"
    assert record_calls[1]["prophet_seed_side"] == "buy"
    # The unwind result is surfaced for operator visibility.
    assert result.payload.get("unwind_status") == "unwound_after_prophet_decline"
    # Confirm was never clicked — the budget trip happened before that.
    assert "click_prophet_confirm" not in ui.calls
