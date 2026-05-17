"""Issue #636/#638: `cmd_create_market_via_ui` is the subprocess-owned
replacement for the legacy agent-driven Playwright runbook.

Since #638, session establishment lives behind the `establish_session`
seam — these tests inject a no-op establish callable that returns a
fresh cache entry, so the inner orchestration is exercised without
touching `SessionCache`, Playwright, or the OTP modal.

The full flow has many seams; these tests pin only the two critical
abort paths so we don't ever leak naked Polymarket exposure:

  1. compute_seed_intent returns blocked → no hedge, no Confirm click.
  2. Hedge fills (`hedge_status=hedged`) but Prophet Confirm fails →
     the Polymarket leg is unwound via a second record_created_market
     call with `prophet_confirm_declined=True`.
"""

from __future__ import annotations

from typing import Any

from agent import AgentConfig, CycleResult, cmd_create_market_via_ui
from arbitrage.intelligence import IntelligenceConfig
from arbitrage.scoring import ScoringConfig
from discovery import AutoDiscoverConfig


def _config() -> AgentConfig:
    return AgentConfig(
        inputs={"prophet_email": "jill@volume.finance", "email_provider": "gmail"},
        project_name="prophet",
        database_name="prophet",
        scoring=ScoringConfig(),
        intelligence=IntelligenceConfig(),
        auto_discover=AutoDiscoverConfig(enabled=True, initial_bet_usdc=1.0),
        live_mode=True,
        max_orders_per_run=5,
        execution_mode="delta_neutral",
        max_hedge_slippage_bps=100.0,
    )


class _FreshCacheEntry:
    """Stand-in cache entry returned by the stub `establish_session`.

    The inner orchestration consumes `cache_entry.jwt` when calling
    `compute_seed_intent`, so we just need anything with a `.jwt` and
    `.refresh_token` truthy.
    """

    jwt = "eyJ.fresh.jwt"
    refresh_token = "rt_fresh"
    prophet_viewer_id = "vid_x"
    state = "fresh"

    def is_fresh(self, *, leeway_seconds: int = 60) -> bool:
        return True


def _stub_establish_session(**_kwargs: Any) -> _FreshCacheEntry:
    """No-op replacement for `establish_browser_session_for_create`.

    Acts as if the cache was fresh and `restore_privy_session` succeeded.
    The browser session arg is the stub `_StubSession`; we don't drive
    it here because the OTP/restore path is exercised in
    `test_establish_session.py`, not this file.
    """
    return _FreshCacheEntry()


class _StubSession:
    def __init__(self) -> None:
        self.urls: list[str] = []
        self.clicks: list[str] = []
        self.fills: list[tuple[str, str]] = []

    def navigate(self, url: str) -> None:
        self.urls.append(url)

    def click(self, selector: str) -> None:
        self.clicks.append(selector)

    def fill(self, selector: str, value: str) -> None:
        self.fills.append((selector, value))

    def wait_for(self, selector: str, *, timeout_ms: int = 30_000) -> None:
        return None

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
    """Captures the order in which the UI helpers are invoked.

    Returns a tunable OCS id (default present) and market redirect id
    (default absent → caller decides per test).
    """

    def __init__(
        self,
        *,
        ocs_id: str = "ocs_test",
        redirect_market_id: str = "",
    ) -> None:
        self.ocs_id = ocs_id
        self.redirect_market_id = redirect_market_id
        self.calls: list[str] = []

    def open_create_form(self, session: Any, *, question: str) -> None:
        self.calls.append(f"open_create_form:{question}")

    def poll_for_ocs_id(self, session: Any, **kwargs: Any) -> str:
        self.calls.append("poll_for_ocs_id")
        return self.ocs_id

    def fill_bet_form(
        self, session: Any, *, seed_side: str, bet_usdc: float
    ) -> None:
        self.calls.append(f"fill_bet_form:{seed_side}:{bet_usdc}")

    def click_prophet_confirm(self, session: Any) -> None:
        self.calls.append("click_prophet_confirm")

    def wait_for_market_redirect(self, session: Any, **kwargs: Any) -> str:
        self.calls.append("wait_for_market_redirect")
        return self.redirect_market_id


def test_create_market_via_ui_no_edge_aborts_without_hedge() -> None:
    record_calls: list[dict[str, Any]] = []

    def stub_compute_seed_intent(**kwargs: Any) -> CycleResult:
        return CycleResult(
            status="blocked",
            reason="no_edge",
            payload={"polymarket_condition_id": kwargs["polymarket_condition_id"]},
        )

    def stub_record_created_market(**kwargs: Any) -> CycleResult:
        record_calls.append(dict(kwargs))
        return CycleResult(status="ok", reason="recorded", payload={})

    ui = _StubCreateMarketUI(ocs_id="ocs_blocked", redirect_market_id="m_unused")
    session = _StubSession()

    result = cmd_create_market_via_ui(
        config=_config(),
        gateway=object(),
        transport=object(),
        polymarket_condition_id="0xCID",
        question="Will X happen?",
        initial_bet_usdc=1.0,
        open_session_factory=lambda: _SessionScope(session),
        establish_session=_stub_establish_session,
        create_market_ui=ui,
        compute_seed_intent=stub_compute_seed_intent,
        record_created_market=stub_record_created_market,
    )

    assert result.status == "blocked"
    assert result.reason == "no_edge"
    # No record_created_market invocation at all (no hedge dispatched).
    assert record_calls == []
    # No Confirm click attempt.
    assert "click_prophet_confirm" not in ui.calls
    assert "fill_bet_form" not in [c.split(":")[0] for c in ui.calls]


def test_create_market_via_ui_unwinds_when_prophet_confirm_fails() -> None:
    def stub_compute_seed_intent(**kwargs: Any) -> CycleResult:
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

    record_calls: list[dict[str, Any]] = []

    def stub_record_created_market(**kwargs: Any) -> CycleResult:
        record_calls.append(dict(kwargs))
        # First call (no prophet_confirm_declined) → hedge succeeded.
        if not kwargs.get("prophet_confirm_declined"):
            return CycleResult(
                status="ok",
                reason="seed_hedge_ready_for_prophet_confirm",
                payload={
                    "hedge_status": "hedged",
                    "next_action": "click_prophet_confirm",
                    "polymarket_order_id": "po_1",
                },
            )
        # Second call (prophet_confirm_declined=True) → unwind path.
        return CycleResult(
            status="ok",
            reason="prophet_confirm_declined",
            payload={"hedge_status": "unwound_after_prophet_decline"},
        )

    # Redirect never happens → wait_for_market_redirect returns "".
    ui = _StubCreateMarketUI(ocs_id="ocs_test", redirect_market_id="")
    session = _StubSession()

    result = cmd_create_market_via_ui(
        config=_config(),
        gateway=object(),
        transport=object(),
        polymarket_condition_id="0xCID",
        question="Will X happen?",
        initial_bet_usdc=1.0,
        open_session_factory=lambda: _SessionScope(session),
        establish_session=_stub_establish_session,
        create_market_ui=ui,
        compute_seed_intent=stub_compute_seed_intent,
        record_created_market=stub_record_created_market,
    )

    assert result.status == "blocked"
    assert result.reason == "prophet_confirm_failed"
    # Exactly two record_created_market invocations: hedge submit + unwind.
    assert len(record_calls) == 2
    assert record_calls[0].get("prophet_confirm_declined") is False
    assert record_calls[1].get("prophet_confirm_declined") is True
    # The unwind reuses the same condition_id + seed_side.
    assert record_calls[1]["polymarket_condition_id"] == "0xCID"
    assert record_calls[1]["prophet_seed_side"] == "buy"
    # Confirm was attempted (otherwise we'd never know it failed).
    assert "click_prophet_confirm" in ui.calls
    assert "wait_for_market_redirect" in ui.calls
    # And the unwind envelope is surfaced for operator visibility.
    assert result.payload.get("unwind_status") == "unwound_after_prophet_decline"
