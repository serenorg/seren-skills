from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_local_module(module_name: str):
    script_dir = Path(__file__).resolve().parents[1] / "kraken" / "smart-dca-bot" / "scripts"
    script_dir_str = str(script_dir)
    sys.path[:] = [script_dir_str, *[path for path in sys.path if path != script_dir_str]]
    for cached_name in ("agent", "serendb_store", "normalized_trade_store"):
        sys.modules.pop(cached_name, None)
    spec = importlib.util.spec_from_file_location(
        f"test_kraken_smart_dca_{module_name}",
        script_dir / f"{module_name}.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_no_db_session_still_emits_trade_report(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYTHONUNBUFFERED", "1")
    store_module = _load_local_module("serendb_store")
    store = store_module.SerenDBStore(None)
    session_id = "run-001"

    store.create_session(session_id, "single_asset", {"dry_run": True})
    store.persist_execution(
        {
            "execution_id": "exec-1",
            "mode": "single_asset",
            "asset": "XBTUSD",
            "target_amount_usd": 50.0,
            "executed_amount_usd": 50.0,
            "executed_price": 50000.0,
            "vwap_at_execution": 50010.0,
            "savings_vs_naive_bps": 8,
            "strategy": "simple",
            "window_start": "2026-03-20T00:00:00Z",
            "window_end": "2026-03-20T00:05:00Z",
            "executed_at": "2026-03-20T00:05:00Z",
            "status": "filled",
            "kraken_order_id": "ord-1",
            "metadata": {
                "session_id": session_id,
                "decision": {"side": "buy", "order_type": "market"},
            },
        }
    )
    store.persist_portfolio_snapshot(
        {
            "session_id": session_id,
            "snapshot_id": "snap-1",
            "total_value_usd": 1500.0,
            "allocations": {"XBTUSD": 1.0},
            "target_allocations": {"XBTUSD": 1.0},
            "drift_max_pct": 0.0,
            "created_at": "2026-03-20T00:06:00Z",
        }
    )
    store.close_session(
        session_id=session_id,
        status="completed",
        total_invested_usd=50.0,
        total_savings_bps=8,
    )

    report_path = tmp_path / "logs" / "trade_reports.jsonl"
    report = json.loads(report_path.read_text(encoding="utf-8").strip())
    assert report["skill_slug"] == "kraken-smart-dca-bot"
    assert report["status"] == "completed"
    assert report["cycle_summary"]["fill_count"] == 1
    assert report["open_positions"][0]["symbol"] == "XBTUSD"
    assert report["trades"][0]["order_id"] == "ord-1"

    stdout = capsys.readouterr().out
    assert "[trade-report] kraken-smart-dca-bot" in stdout
