from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

FIXTURE_DIR = Path(__file__).parent / "fixtures"
SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "agent.py"


def _read_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def test_happy_path_fixture_is_successful() -> None:
    payload = _read_fixture("happy_path.json")
    assert payload["status"] == "ok"
    assert payload["skill"] == "polymarket-maker-rebate-bot"
    assert payload["mode"] == "dry-run"


def test_negative_edge_fixture_skips_all_quotes() -> None:
    payload = _read_fixture("negative_edge.json")
    assert payload["status"] == "ok"
    assert payload["strategy_summary"]["markets_quoted"] == 0
    assert payload["strategy_summary"]["markets_skipped"] >= 1


def test_live_guard_fixture_blocks_execution() -> None:
    payload = _read_fixture("live_guard.json")
    assert payload["status"] == "error"
    assert payload["error_code"] == "live_confirmation_required"


def test_backtest_run_type_returns_result_from_config_history(tmp_path: Path) -> None:
    now_ts = int(time.time())
    start_ts = now_ts - (90 * 24 * 3600)
    history = []
    for i in range(720):
        wave = ((i % 24) - 12) / 600.0
        drift = ((i % 11) - 5) / 3000.0
        px = max(0.05, min(0.95, 0.5 + wave + drift))
        history.append({"t": start_ts + (i * 3600), "p": round(px, 6)})

    payload = {
        "execution": {"dry_run": True, "live_mode": False},
        "backtest": {
            "days": 90,
            "fidelity_minutes": 60,
            "participation_rate": 0.2,
            "volatility_window_points": 24,
            "min_history_points": 200,
            "min_liquidity_usd": 0,
        },
        "strategy": {
            "bankroll_usd": 1000,
            "markets_max": 4,
            "min_seconds_to_resolution": 21600,
            "min_edge_bps": 2,
            "default_rebate_bps": 3,
            "expected_unwind_cost_bps": 1.5,
            "adverse_selection_bps": 1.0,
            "min_spread_bps": 20,
            "max_spread_bps": 150,
            "volatility_spread_multiplier": 0.35,
            "base_order_notional_usd": 25,
            "max_notional_per_market_usd": 125,
            "max_total_notional_usd": 500,
            "max_position_notional_usd": 150,
            "inventory_skew_strength_bps": 25,
        },
        "backtest_markets": [
            {
                "market_id": "TEST-90D",
                "question": "Synthetic 90D market",
                "token_id": "TEST-90D",
                "rebate_bps": 3,
                "end_ts": now_ts + (7 * 24 * 3600),
                "history": history,
            }
        ],
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(payload), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--config",
            str(config_path),
            "--run-type",
            "backtest",
            "--backtest-days",
            "90",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["status"] == "ok"
    assert output["mode"] == "backtest"
    assert output["backtest_summary"]["days"] == 90
    assert output["backtest_summary"]["source"] == "config"
    assert output["backtest_summary"]["markets_selected"] >= 1
    assert output["results"]["events"] > 0
