from __future__ import annotations

from typing import Any

import polymarket_live


def test_marketable_exit_uses_tick_size_best_bid_and_visible_depth(monkeypatch) -> None:
    raw_book = {
        "bids": [
            {"price": "0.42", "size": "1.5"},
            {"price": "0.40", "size": "2.0"},
        ],
        "asks": [{"price": "0.45", "size": "4.0"}],
        "tick_size": "0.001",
        "neg_risk": True,
    }
    monkeypatch.setattr(
        polymarket_live,
        "fetch_book",
        lambda *_args, **_kwargs: polymarket_live.parse_book_payload(raw_book),
    )
    monkeypatch.setattr(
        polymarket_live,
        "fetch_fee_rate_bps",
        lambda *_args, **_kwargs: 0,
    )

    plan = polymarket_live.build_marketable_sell_order("token-1", 3.5)

    assert plan["price"] == 0.001
    assert plan["tick_size"] == "0.001"
    assert plan["best_bid"] == 0.42
    assert plan["best_ask"] == 0.45
    assert plan["estimated_fill_size"] == 3.5
    assert plan["estimated_unfilled_size"] == 0.0
    assert plan["estimated_exit_value_usd"] == 1.43
    assert plan["execution_style"] == "marketable-limit-min-tick"


def test_unwind_held_inventory_uses_non_passive_marketable_sell(monkeypatch) -> None:
    def _sell_plan(token_id: str, shares: float, **_kwargs: Any) -> dict[str, Any]:
        return {
            "token_id": token_id,
            "shares": shares,
            "price": 0.01,
            "tick_size": "0.01",
            "neg_risk": False,
            "fee_rate_bps": 0,
            "best_bid": 0.38,
            "best_ask": 0.41,
            "estimated_exit_value_usd": 1.52,
            "estimated_fill_size": shares,
            "estimated_unfilled_size": 0.0,
            "estimated_average_price": 0.38,
            "execution_style": "marketable-limit-min-tick",
        }

    class RecordingTrader:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def create_order(self, **kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            return {"id": "exit-order-1"}

    monkeypatch.setattr(polymarket_live, "build_marketable_sell_order", _sell_plan)
    trader = RecordingTrader()

    sells = polymarket_live.sell_held_inventory(
        trader=trader,
        raw_positions=[
            {"token_id": "covered-token", "shares": 10},
            {"token_id": "exit-token", "shares": 4},
        ],
        covered_token_ids={"covered-token"},
    )

    assert len(sells) == 1
    assert trader.calls == [
        {
            "token_id": "exit-token",
            "side": "SELL",
            "price": 0.01,
            "size": 4,
            "tick_size": "0.01",
            "neg_risk": False,
            "fee_rate_bps": 0,
            "post_only": False,
        }
    ]
    assert sells[0]["estimated_exit_value_usd"] == 1.52
    assert sells[0]["estimated_fill_size"] == 4
    assert sells[0]["execution_style"] == "marketable-limit-min-tick"
