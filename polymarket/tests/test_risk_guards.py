"""Tests for polymarket/_shared/risk_guards.py — critical paths only."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "_shared")
)
from risk_guards import (
    auto_pause_cron,
    check_drawdown_stop_loss,
    check_position_age,
    sync_position_timestamps,
)


# ── drawdown stop-loss ──────────────────────────────────────────────


class TestDrawdownStopLoss:
    def test_triggers_unwind_when_limit_breached(self):
        unwind = MagicMock(return_value={"status": "unwound"})
        result = check_drawdown_stop_loss(
            live_risk={"drawdown_pct": 20.0, "current_equity_usd": 80, "peak_equity_usd": 100},
            max_drawdown_pct=15.0,
            unwind_fn=unwind,
            log_fn=lambda _: None,
        )
        unwind.assert_called_once()
        assert result == {"status": "unwound"}

    def test_no_action_below_limit(self):
        unwind = MagicMock()
        result = check_drawdown_stop_loss(
            live_risk={"drawdown_pct": 5.0},
            max_drawdown_pct=15.0,
            unwind_fn=unwind,
        )
        unwind.assert_not_called()
        assert result is None

    def test_disabled_when_limit_zero(self):
        unwind = MagicMock()
        result = check_drawdown_stop_loss(
            live_risk={"drawdown_pct": 99.0},
            max_drawdown_pct=0,
            unwind_fn=unwind,
        )
        unwind.assert_not_called()
        assert result is None


# ── position aging ──────────────────────────────────────────────────


class TestPositionAge:
    def test_detects_aged_positions(self):
        now = datetime(2026, 3, 30, 12, 0, tzinfo=timezone.utc)
        old = (now - timedelta(hours=80)).isoformat()
        fresh = (now - timedelta(hours=10)).isoformat()
        aged = check_position_age(
            position_timestamps={"old_tok": old, "fresh_tok": fresh},
            current_exposure={"old_tok": 50.0, "fresh_tok": 30.0},
            max_age_hours=72,
            now=now,
        )
        assert aged == ["old_tok"]

    def test_ignores_zero_exposure(self):
        now = datetime(2026, 3, 30, 12, 0, tzinfo=timezone.utc)
        old = (now - timedelta(hours=100)).isoformat()
        aged = check_position_age(
            position_timestamps={"tok": old},
            current_exposure={"tok": 0.0},
            max_age_hours=72,
            now=now,
        )
        assert aged == []

    def test_disabled_when_zero(self):
        aged = check_position_age(
            position_timestamps={"tok": "2020-01-01T00:00:00+00:00"},
            current_exposure={"tok": 100.0},
            max_age_hours=0,
        )
        assert aged == []


# ── timestamp sync ──────────────────────────────────────────────────


class TestSyncTimestamps:
    def test_adds_new_prunes_closed(self):
        now = datetime(2026, 3, 30, 12, 0, tzinfo=timezone.utc)
        result = sync_position_timestamps(
            position_timestamps={"existing": "2026-03-29T00:00:00+00:00", "closed": "2026-03-28T00:00:00+00:00"},
            current_exposure={"existing": 50.0, "brand_new": 30.0},
            now=now,
        )
        assert result["existing"] == "2026-03-29T00:00:00+00:00"
        assert result["brand_new"] == now.isoformat()
        assert "closed" not in result


# ── cron auto-pause ─────────────────────────────────────────────────


class TestCronAutoPause:
    def test_pauses_on_low_serenbucks(self):
        pause = MagicMock()
        paused = auto_pause_cron(
            serenbucks_balance=0.50,
            trading_balance=100.0,
            min_serenbucks=1.0,
            job_id="job-123",
            pause_fn=pause,
            log_fn=lambda _: None,
        )
        assert paused is True
        pause.assert_called_once_with("job-123")

    def test_no_pause_when_funded(self):
        pause = MagicMock()
        paused = auto_pause_cron(
            serenbucks_balance=10.0,
            trading_balance=100.0,
            min_serenbucks=1.0,
            job_id="job-123",
            pause_fn=pause,
        )
        assert paused is False
        pause.assert_not_called()

    def test_no_pause_without_job_id(self):
        paused = auto_pause_cron(
            serenbucks_balance=0.0,
            trading_balance=0.0,
            min_serenbucks=1.0,
            job_id=None,
            pause_fn=MagicMock(),
        )
        assert paused is False
