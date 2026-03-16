from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SHARED_PATH = Path(__file__).resolve().parents[1] / "polymarket_live.py"


def _load_shared_module():
    spec = importlib.util.spec_from_file_location("polymarket_live_shared", SHARED_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_execute_single_market_quotes_skips_buy_when_cash_reserve_is_hit() -> None:
    module = _load_shared_module()

    class FakeTrader:
        def __init__(self) -> None:
            self.orders: list[dict[str, object]] = []

        def get_positions(self):
            return []

        def get_cash_balance(self) -> float:
            return 50.0

        def cancel_all(self):
            return {"cancelled": True}

        def get_orders(self):
            return []

        def create_order(self, **kwargs):
            self.orders.append(kwargs)
            return {"id": "ORDER-1"}

    result = module.execute_single_market_quotes(
        trader=FakeTrader(),
        quotes=[
            {
                "market_id": "LIVE-MKT-1",
                "bid_notional_usd": 30.0,
                "ask_notional_usd": 0.0,
                "bid_price": 0.5,
                "ask_price": 0.52,
            }
        ],
        markets=[
            {
                "market_id": "LIVE-MKT-1",
                "token_id": "TOKEN-LIVE-1",
                "mid_price": 0.5,
                "best_bid": 0.49,
                "best_ask": 0.51,
                "tick_size": "0.01",
                "neg_risk": False,
            }
        ],
        execution_settings=module.LiveExecutionSettings(
            cancel_before_requote=False,
            min_cash_reserve_usd=30.0,
        ),
    )

    assert result["status"] == "ok"
    assert result["orders_submitted"] == []
    assert result["order_skips"][0]["reason"] == "cash_reserve_guard"
    assert result["live_risk"]["cash_balance_usd"] == 50.0
    assert result["live_risk"]["current_equity_usd"] == 50.0


def test_capture_live_risk_blocks_on_drawdown_limit() -> None:
    module = _load_shared_module()

    class FakeTrader:
        def get_cash_balance(self) -> float:
            return 60.0

    result = module._capture_live_risk(
        trader=FakeTrader(),
        exposure_by_key={"TOKEN-LIVE-1": 20.0},
        execution_settings=module.LiveExecutionSettings(
            prior_peak_equity_usd=120.0,
            max_live_drawdown_pct=20.0,
        ),
    )

    assert result["status"] == "error"
    assert result["error_code"] == "live_drawdown_limit_breached"
    assert result["state"]["current_equity_usd"] == 80.0
    assert result["state"]["drawdown_pct"] == 33.3333


def test_execute_single_market_quotes_cancels_after_timeout(monkeypatch) -> None:
    module = _load_shared_module()
    cleanup_calls: list[str] = []

    class FakeTrader:
        def get_positions(self):
            return []

        def get_cash_balance(self) -> float:
            return 100.0

        def cancel_all(self):
            cleanup_calls.append("cancel")
            return {"cancelled": True}

        def get_orders(self):
            return []

        def create_order(self, **kwargs):
            return {"id": "ORDER-NEVER"}

    def fake_invoke_trader_call(operation_name, func, execution_settings):
        del execution_settings
        if operation_name.startswith("create_order_buy"):
            raise TimeoutError("create_order_buy:LIVE-MKT-1 timed out after 0.01s")
        return func()

    monkeypatch.setattr(module, "_invoke_trader_call", fake_invoke_trader_call)

    result = module.execute_single_market_quotes(
        trader=FakeTrader(),
        quotes=[
            {
                "market_id": "LIVE-MKT-1",
                "bid_notional_usd": 25.0,
                "ask_notional_usd": 0.0,
                "bid_price": 0.5,
                "ask_price": 0.52,
            }
        ],
        markets=[
            {
                "market_id": "LIVE-MKT-1",
                "token_id": "TOKEN-LIVE-1",
                "mid_price": 0.5,
                "best_bid": 0.49,
                "best_ask": 0.51,
                "tick_size": "0.01",
                "neg_risk": False,
            }
        ],
        execution_settings=module.LiveExecutionSettings(
            cancel_before_requote=False,
            cancel_on_error=True,
        ),
    )

    assert result["status"] == "error"
    assert result["error_code"] == "live_operation_timeout"
    assert result["cleanup_cancel_all"] == {"cancelled": True}
    assert cleanup_calls == ["cancel"]
