"""Critical monitoring and tracker tests for polymarket-bot."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from agent import TradingAgent
from position_tracker import PositionTracker


class _FakePositionsClient:
    def get_positions(self):
        return [
            {
                "conditionId": "market-1",
                "asset_id": "token-no-1",
                "question": "Will outcome happen?",
                "outcome": "No",
                "avgPrice": "0.08",
                "currentValue": "12.0",
                "size": "100",
                "event_id": "event-1",
            }
        ]

    def get_midpoint(self, token_id):
        raise AssertionError("currentValue/size should be enough to infer the mark")


def test_position_tracker_sync_uses_live_token_side_and_prices(tmp_path):
    tracker = PositionTracker(
        positions_file=str(tmp_path / "positions.json"),
        use_serendb=False,
    )

    summary = tracker.sync_with_polymarket(_FakePositionsClient())

    assert summary == {"added": 1, "removed": 0, "updated": 0}
    position = tracker.get_position("market-1")
    assert position is not None
    assert position.side == "NO"
    assert position.thesis_side == "SELL"
    assert position.entry_price == pytest.approx(0.08)
    assert position.current_price == pytest.approx(0.12)
    assert position.quantity == pytest.approx(100.0)
    assert position.unrealized_pnl == pytest.approx(4.0)


def test_evaluate_opportunity_blocks_event_clustering():
    agent = TradingAgent.__new__(TradingAgent)
    agent.min_buy_price = 0.02
    agent.max_divergence = 0.50
    agent.effective_mispricing_threshold = 0.08
    agent.min_annualized_return = 0.25
    agent.min_edge_to_spread_ratio = 3.0
    agent.max_positions_per_event = 1
    agent.max_positions = 10
    agent.stop_loss_bankroll = 0.0
    agent.bankroll = 100.0
    agent.max_kelly_fraction = 0.06
    agent.max_depth_fraction = 0.25
    agent.positions = MagicMock()
    agent.positions.has_exposure.return_value = False
    agent.positions.get_all_positions.return_value = [SimpleNamespace(event_id="event-1")]
    agent.positions.get_current_bankroll.return_value = 100.0
    agent.positions.get_available_capital.return_value = 100.0
    agent.polymarket = MagicMock()
    agent.polymarket.get_book_metrics.return_value = {
        "best_bid": 0.48,
        "best_ask": 0.50,
        "spread": 0.02,
        "bid_depth_usd": 1000.0,
        "ask_depth_usd": 1000.0,
    }

    market = {
        "market_id": "market-2",
        "token_id": "token-yes-2",
        "no_token_id": "token-no-2",
        "event_id": "event-1",
        "price": 0.50,
        "question": "Clustered market",
        "days_to_resolution": 30,
    }

    result = agent.evaluate_opportunity(
        market,
        "research",
        fair_value=0.65,
        confidence="high",
    )

    assert result is None


def test_monitor_existing_risk_cancels_stale_orders_and_triggers_take_profit(monkeypatch, tmp_path):
    import polymarket_live

    agent = TradingAgent.__new__(TradingAgent)
    agent.dry_run = False
    agent.logs_dir = tmp_path / "logs"
    agent.state_file = agent.logs_dir / "runtime_state.json"
    agent.runtime_state = {
        "order_timestamps": {
            "old-order": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
        },
        "position_alerts": {},
        "pending_exit_markets": {},
    }
    agent.stale_order_max_age_seconds = 1800
    agent.take_profit_pct = 0.10
    agent.position_stop_loss_pct = 0.10
    agent.alert_move_pct = 0.08
    agent.max_position_age_hours = 72.0
    agent.near_resolution_hours = 24.0
    agent.logger = MagicMock()
    agent.polymarket = MagicMock()
    agent.polymarket.get_open_orders.return_value = []
    agent.polymarket._require_trader.return_value = MagicMock()
    agent.positions = MagicMock()
    live_position = SimpleNamespace(
        market="Winner market",
        market_id="market-3",
        token_id="token-3",
        quantity=100.0,
        size=10.0,
        unrealized_pnl=2.0,
        side="YES",
        entry_price=0.10,
        current_price=0.12,
        opened_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        end_date="",
    )
    agent.positions.sync_with_polymarket.return_value = {"added": 0, "removed": 0, "updated": 1}
    agent.positions.get_all_positions.return_value = [live_position]
    agent._close_position_via_guard = MagicMock(return_value={"market_id": "market-3", "reason": "take-profit"})

    monkeypatch.setattr(
        polymarket_live,
        "cancel_stale_orders",
        lambda **kwargs: {
            "stale_count": 1,
            "cancelled": [{"order_id": "old-order", "status": "cancelled"}],
        },
    )

    summary = agent.monitor_existing_risk()

    assert summary["stale_orders_cancelled"] == 1
    assert len(summary["guard_exits"]) == 1
    agent._close_position_via_guard.assert_called_once()
    assert agent.logger.log_notification.call_count >= 2
