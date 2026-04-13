from __future__ import annotations

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIRS = [
    "alpaca/saas-short-trader",
    "alpaca/sass-short-trader-delta-neutral",
]


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=SKILL_DIRS)
def test_schema_has_explicit_run_type_and_scan_run_id(skill_dir: str) -> None:
    sql = (REPO_ROOT / skill_dir / "scripts/serendb_schema.sql").read_text(encoding="utf-8")
    assert "ALTER TABLE trading.strategy_runs ADD COLUMN IF NOT EXISTS run_type TEXT;" in sql
    assert "ALTER TABLE trading.position_marks_daily ADD COLUMN IF NOT EXISTS scan_run_id UUID REFERENCES trading.strategy_runs(run_id);" in sql
    assert "ALTER TABLE trading.pnl_daily ADD COLUMN IF NOT EXISTS scan_run_id UUID REFERENCES trading.strategy_runs(run_id);" in sql


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=SKILL_DIRS)
def test_storage_queries_fallback_to_explicit_run_type(skill_dir: str) -> None:
    source = (REPO_ROOT / skill_dir / "scripts/serendb_storage.py").read_text(encoding="utf-8")
    assert "COALESCE(run_type, metadata->>'run_type', '')" in source
    assert "COALESCE(sr.run_type, sr.metadata->>'run_type', '') = 'scan'" in source


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=SKILL_DIRS)
def test_learning_labels_join_via_scan_run_id_fallback(skill_dir: str) -> None:
    source = (REPO_ROOT / skill_dir / "scripts/self_learning.py").read_text(encoding="utf-8")
    assert "COALESCE(sr.run_type, sr.metadata->>'run_type', 'scan')" in source
    assert "COALESCE(pm.scan_run_id, pm.source_run_id) = fs.run_id" in source
