from __future__ import annotations

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
ALPACA_SCHEMA_FILES = [
    "alpaca/saas-short-trader/scripts/serendb_schema.sql",
    "alpaca/sass-short-trader-delta-neutral/scripts/serendb_schema.sql",
]

REQUIRED_STRATEGY_RUN_COLUMNS = [
    "skill_slug",
    "venue",
    "dry_run",
    "started_at",
    "completed_at",
    "config",
    "summary",
    "error_code",
    "error_message",
]

REQUIRED_ORDER_EVENT_COLUMNS = [
    "order_id",
    "instrument_id",
    "symbol",
    "event_type",
    "price",
    "quantity",
    "notional_usd",
    "metadata",
]


@pytest.mark.parametrize("rel_path", ALPACA_SCHEMA_FILES, ids=ALPACA_SCHEMA_FILES)
def test_strategy_runs_schema_uses_additive_migrations(rel_path: str) -> None:
    sql = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
    for column in REQUIRED_STRATEGY_RUN_COLUMNS:
        assert (
            f"ALTER TABLE trading.strategy_runs ADD COLUMN IF NOT EXISTS {column} " in sql
        ), f"{rel_path} is missing additive migration for trading.strategy_runs.{column}"


@pytest.mark.parametrize("rel_path", ALPACA_SCHEMA_FILES, ids=ALPACA_SCHEMA_FILES)
def test_order_events_schema_uses_additive_migrations(rel_path: str) -> None:
    sql = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
    for column in REQUIRED_ORDER_EVENT_COLUMNS:
        assert (
            f"ALTER TABLE trading.order_events ADD COLUMN IF NOT EXISTS {column} " in sql
        ), f"{rel_path} is missing additive migration for trading.order_events.{column}"
