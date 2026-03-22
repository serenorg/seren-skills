from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_local_module(module_name: str):
    script_dir = Path(__file__).resolve().parents[1] / "scripts"
    script_dir_str = str(script_dir)
    sys.path[:] = [script_dir_str, *[path for path in sys.path if path != script_dir_str]]
    for cached_name in ("serendb_store", "trade_reporting"):
        sys.modules.pop(cached_name, None)
    spec = importlib.util.spec_from_file_location(f"test_grid_{module_name}", script_dir / f"{module_name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _make_store(tmp_path):
    store_module = _load_local_module("serendb_store")
    reporting_module = _load_local_module("trade_reporting")
    store = object.__new__(store_module.SerenDBStore)
    store._execute_sql = lambda query: None
    store._sql_text = store_module.SerenDBStore._sql_text
    store._sql_bool = store_module.SerenDBStore._sql_bool
    store._sql_json = store_module.SerenDBStore._sql_json
    store._normalized_terminal_status = store_module.SerenDBStore._normalized_terminal_status
    store._reporter = reporting_module.CycleTradeReportEmitter(
        skill_slug="kraken-grid-trader",
        venue="kraken",
        strategy_name="grid-trader",
        logs_dir=str(tmp_path / "logs"),
    )
    return store_module, store


def test_grid_store_emits_cycle_and_terminal_reports(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("PYTHONUNBUFFERED", "1")
    store_module, store = _make_store(tmp_path)

    store_module.SerenDBStore.create_session(
        store,
        "00000000-0000-0000-0000-000000000001",
        "grid-cycle",
        "XBTUSD",
        False,
    )
    store_module.SerenDBStore.save_order(
        store,
        "00000000-0000-0000-0000-000000000001",
        "ord-1",
        "buy",
        50000.0,
        0.01,
        "placed",
        {"pair": "XBTUSD", "order_type": "limit"},
    )
    store_module.SerenDBStore.save_fill(
        store,
        "00000000-0000-0000-0000-000000000001",
        "ord-1",
        "buy",
        50000.0,
        0.01,
        0.8,
        500.0,
        {"pair": "XBTUSD"},
    )
    store_module.SerenDBStore.save_position(
        store,
        "00000000-0000-0000-0000-000000000001",
        "XBTUSD",
        0.01,
        1000.0,
        1500.0,
        25.0,
        3,
    )
    store_module.SerenDBStore.save_event(
        store,
        "00000000-0000-0000-0000-000000000001",
        "stop_loss_triggered",
        {"pair": "XBTUSD", "error_message": "stop loss triggered"},
    )

    report_path = tmp_path / "logs" / "trade_reports.jsonl"
    lines = [json.loads(line) for line in report_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 2
    assert lines[0]["cycle_summary"]["fill_count"] == 1
    assert lines[0]["status"] == "running"
    assert lines[1]["status"] == "stopped"
    assert lines[1]["cycle_summary"]["halt_reason"] == "stop loss triggered"

    stdout = capsys.readouterr().out
    assert "[trade-report] kraken-grid-trader" in stdout
