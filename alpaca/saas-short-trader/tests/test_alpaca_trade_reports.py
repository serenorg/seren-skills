from __future__ import annotations

import importlib.util
import json
import sys
import types
from datetime import date
from pathlib import Path


def _load_module(script_dir: Path, module_name: str):
    sys.path[:] = [str(script_dir), *[path for path in sys.path if path != str(script_dir)]]
    for cached_name in ("trade_reporting", "serendb_storage"):
        sys.modules.pop(cached_name, None)
    spec = importlib.util.spec_from_file_location(module_name, script_dir / f"{module_name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class _FakeCursor:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params=None):
        del query, params


class _FakeConn:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return _FakeCursor()

    def commit(self):
        return None


def test_storage_emits_trade_report_on_terminal_status(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYTHONUNBUFFERED", "1")
    psycopg_stub = types.ModuleType("psycopg")
    psycopg_stub.connect = lambda *args, **kwargs: _FakeConn()
    rows_stub = types.ModuleType("psycopg.rows")
    rows_stub.dict_row = object()
    sys.modules["psycopg"] = psycopg_stub
    sys.modules["psycopg.rows"] = rows_stub

    script_dir = Path(__file__).resolve().parents[1] / "scripts"
    storage_module = _load_module(script_dir, "serendb_storage")
    storage = storage_module.SerenDBStorage("postgresql://unused")
    storage.connect = lambda: _FakeConn()

    run_id = storage.insert_run(
        mode="paper-sim",
        universe=["SNOW"],
        max_names_scored=30,
        max_names_orders=8,
        min_conviction=65.0,
        status="running",
        metadata={"run_type": "scan"},
    )
    storage.insert_order_events(
        run_id,
        "paper-sim",
        [
            {
                "order_ref": "SNOW-open",
                "ticker": "SNOW",
                "side": "SELL",
                "order_type": "limit",
                "status": "planned",
                "qty": 10.0,
                "filled_qty": 10.0,
                "filled_avg_price": 120.0,
                "limit_price": 120.0,
                "details": {"entry_price": 120.0, "planned_notional_usd": 1200.0},
            }
        ],
    )
    storage.upsert_position_marks(
        date(2026, 3, 20),
        "paper-sim",
        [
            {
                "ticker": "SNOW",
                "qty": 10.0,
                "avg_entry_price": 120.0,
                "mark_price": 110.0,
                "market_value": 1100.0,
                "realized_pnl": 0.0,
                "unrealized_pnl": 100.0,
                "gross_exposure": 1200.0,
                "net_exposure": -1200.0,
            }
        ],
        source_run_id=run_id,
    )
    storage.upsert_pnl_daily(
        as_of_date=date(2026, 3, 20),
        mode="paper-sim",
        realized_pnl=0.0,
        unrealized_pnl=100.0,
        gross_exposure=1200.0,
        net_exposure=-1200.0,
        hit_rate=1.0,
        max_drawdown=25.0,
        source_run_id=run_id,
    )
    storage.update_run_status(run_id, "completed", {"selected_count": 1})

    report_path = tmp_path / "logs" / "trade_reports.jsonl"
    report = json.loads(report_path.read_text(encoding="utf-8").strip())
    assert report["skill_slug"] == "alpaca-saas-short-trader"
    assert report["status"] == "completed"
    assert report["cycle_summary"]["fill_count"] == 1
    assert report["open_positions"][0]["symbol"] == "SNOW"
    assert report["trades"][0]["order_id"] == "SNOW-open"

    stdout = capsys.readouterr().out
    assert "[trade-report] alpaca-saas-short-trader" in stdout
