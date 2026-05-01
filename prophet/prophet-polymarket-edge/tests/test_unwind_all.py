"""Critical tests for --unwind-all (issue #460).

Coverage map (acceptance criterion -> test):

- AC --unwind-all without --yes-live blocks         -> test_unwind_requires_yes_live_confirmation
- AC cancel_all called before any sell              -> test_unwind_cancels_orders_before_selling
- AC every held position becomes a marketable SELL  -> test_unwind_submits_sell_per_held_position
- AC sell uses min-tick from live order book        -> test_marketable_sell_uses_min_tick_from_live_book
- AC empty positions => no orders, no error         -> test_unwind_with_no_positions_succeeds
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "agent.py"


def _load_agent_module(name: str = "prophet_polymarket_edge_agent_for_unwind"):
    spec = importlib.util.spec_from_file_location(name, SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def agent():
    return _load_agent_module()


def test_unwind_requires_yes_live_confirmation(agent, capsys) -> None:
    """`--unwind-all` without `--yes-live` must refuse and exit non-zero.

    Mirrors the maker-rebate-bot confirmation gate. An accidental
    `--unwind-all` should never actually liquidate.
    """
    rc = agent.main(["--unwind-all", "--config", "/dev/null"])
    out = capsys.readouterr().out
    assert rc == 1
    payload = json.loads(out)
    assert payload["status"] == "error"
    assert payload["error_code"] == "unwind_confirmation_required"


def test_unwind_cancels_orders_before_selling(agent, monkeypatch) -> None:
    """`cancel_all` must run before the position sweep so resting maker orders
    cannot fill on the same tick we're sweeping the book.
    """
    call_order: List[str] = []

    class StubTrader:
        def __init__(self) -> None:
            self.address = "0xdead"

        def cancel_all(self) -> Any:
            call_order.append("cancel_all")
            return {"canceled": []}

        def get_positions(self) -> Any:
            call_order.append("get_positions")
            return []

        def create_order(self, **_: Any) -> Any:  # pragma: no cover - no positions
            call_order.append("create_order")
            return {}

    monkeypatch.setattr(agent, "DirectClobTrader", StubTrader)

    result = agent.run_unwind_all(config={})
    assert result["status"] == "ok"
    assert call_order[0] == "cancel_all"
    assert "get_positions" in call_order
    assert call_order.index("cancel_all") < call_order.index("get_positions")


def test_unwind_submits_sell_per_held_position(agent, monkeypatch) -> None:
    """For every held position with size > 0, a marketable SELL is submitted.

    Zero-size rows are skipped silently. The result includes one
    `sell_results` entry per submitted order.
    """
    submitted: List[Dict[str, Any]] = []

    class StubTrader:
        def __init__(self) -> None:
            self.address = "0xdead"

        def cancel_all(self) -> Any:
            return {"canceled": []}

        def get_positions(self) -> Any:
            return [
                {"token_id": "0xtok_a", "size": 100.0},
                {"token_id": "0xtok_b", "size": 50.0},
                {"token_id": "0xtok_c", "size": 0.0},  # must be skipped
            ]

        def create_order(self, **kwargs: Any) -> Any:
            submitted.append(kwargs)
            return {"order_id": f"order-{len(submitted)}", "status": "submitted"}

    def fake_build(token_id: str, shares: float) -> Dict[str, Any]:
        return {
            "token_id": token_id,
            "shares": shares,
            "price": 0.01,
            "tick_size": "0.01",
            "best_bid": 0.45,
            "estimated_exit_value_usd": shares * 0.45,
            "estimated_fill_size": shares,
            "estimated_unfilled_size": 0.0,
            "estimated_average_price": 0.45,
            "execution_style": "marketable-limit-min-tick",
        }

    monkeypatch.setattr(agent, "DirectClobTrader", StubTrader)
    monkeypatch.setattr(agent, "build_marketable_sell_order", fake_build)

    result = agent.run_unwind_all(config={})
    assert result["status"] == "ok"
    assert result["positions_unwound"] == 2  # zero-size row excluded
    assert len(submitted) == 2
    assert {o["token_id"] for o in submitted} == {"0xtok_a", "0xtok_b"}
    assert all(o["side"] == "SELL" for o in submitted)


def test_marketable_sell_uses_min_tick_from_live_book(agent, monkeypatch) -> None:
    """`build_marketable_sell_order` must price at the market's current
    `tick_size`, not a hardcoded $0.001. CLOB Exit Rules require this so
    we never quote below the market's actual minimum tick.
    """
    book_payload = {
        "best_bid": 0.42,
        "tick_size": "0.001",
        "raw": {
            "bids": [{"price": "0.42", "size": "200"}],
            "asks": [{"price": "0.45", "size": "100"}],
            "tick_size": "0.001",
        },
    }
    monkeypatch.setattr(agent, "fetch_book", lambda token_id: book_payload)

    plan = agent.build_marketable_sell_order("0xtok", 100.0)
    assert plan["price"] == 0.001  # snapped to live tick_size, NOT hardcoded 0.01
    assert plan["tick_size"] == "0.001"
    assert plan["best_bid"] == 0.42
    assert plan["execution_style"] == "marketable-limit-min-tick"


def test_unwind_with_no_positions_succeeds(agent, monkeypatch) -> None:
    """An unwind triggered when the wallet holds no positions still cancels
    orders and returns status=ok with zero sells. This is the common case
    after a clean session and must not raise.
    """

    class StubTrader:
        def __init__(self) -> None:
            self.address = "0xdead"

        def cancel_all(self) -> Any:
            return {"canceled": []}

        def get_positions(self) -> Any:
            return []

        def create_order(self, **_: Any) -> Any:  # pragma: no cover - never called
            raise AssertionError("create_order must not be called when no positions are held")

    monkeypatch.setattr(agent, "DirectClobTrader", StubTrader)

    result = agent.run_unwind_all(config={})
    assert result["status"] == "ok"
    assert result["positions_unwound"] == 0
    assert result["sell_results"] == []
    assert "cancel_all" in result
