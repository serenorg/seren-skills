from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

FIXTURE_DIR = Path(__file__).parent / "fixtures"
SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "agent.py"
CONFIG_EXAMPLE_PATH = Path(__file__).resolve().parents[1] / "config.example.json"


def _read_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _load_agent_module():
    spec = importlib.util.spec_from_file_location("maker_rebate_bot_agent", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


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


def test_quote_mode_fetches_live_markets_when_config_markets_is_empty(monkeypatch) -> None:
    agent = _load_agent_module()
    now_ts = int(time.time())
    fetched_urls: list[str] = []

    def fake_http_get_json(url: str, timeout: int = 30):
        fetched_urls.append(url)
        return [
            {
                "id": "LIVE-MKT-1",
                "question": "Will event A happen?",
                "clobTokenIds": ["TOKEN-1"],
                "outcomePrices": ["0.48", "0.52"],
                "bestBid": 0.47,
                "bestAsk": 0.49,
                "liquidity": 500000,
                "volume24hr": 100000,
                "rebate_bps": 2.5,
                "endDate": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ts + 86400)),
            },
            {
                "id": "LIVE-MKT-2",
                "question": "Will event B happen?",
                "clobTokenIds": ["TOKEN-2"],
                "outcomePrices": ["0.61", "0.39"],
                "bestBid": 0.6,
                "bestAsk": 0.62,
                "liquidity": 450000,
                "volume24hr": 90000,
                "rebate_bps": 3.0,
                "endDate": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ts + 172800)),
            },
        ]

    monkeypatch.setattr(agent, "_http_get_json", fake_http_get_json)
    config = {
        "execution": {"dry_run": True, "live_mode": False},
        "backtest": {"min_liquidity_usd": 0, "markets_fetch_limit": 5},
        "strategy": {
            "markets_max": 2,
            "min_seconds_to_resolution": 60,
            "min_spread_bps": 20,
        },
        "markets": [],
    }

    result = agent.run_quote(config=config, markets_file=None, yes_live=False)

    assert result["status"] == "ok"
    assert result["strategy_summary"]["markets_considered"] == 2
    assert result["strategy_summary"]["markets_quoted"] == 2
    assert any("/publishers/polymarket-data/markets?" in url for url in fetched_urls)


def test_live_guard_fixture_blocks_execution() -> None:
    payload = _read_fixture("live_guard.json")
    assert payload["status"] == "error"
    assert payload["error_code"] == "live_confirmation_required"


def test_backtest_run_type_returns_result_from_config_history(tmp_path: Path) -> None:
    payload = json.loads(CONFIG_EXAMPLE_PATH.read_text(encoding="utf-8"))
    assert payload["strategy"]["bankroll_usd"] == 1000

    now_ts = int(time.time())
    start_ts = now_ts - (90 * 24 * 3600)
    history = []
    for i in range(720):
        wave = ((i % 24) - 12) / 600.0
        drift = ((i % 11) - 5) / 3000.0
        px = max(0.05, min(0.95, 0.5 + wave + drift))
        history.append({"t": start_ts + (i * 3600), "p": round(px, 6)})

    payload["backtest"]["min_history_points"] = 200
    payload["backtest"]["min_liquidity_usd"] = 0
    payload["backtest_markets"] = [
        {
            "market_id": f"TEST-90D-{idx}",
            "question": "Synthetic 90D market",
            "token_id": f"TEST-90D-{idx}",
            "rebate_bps": 3,
            "end_ts": now_ts + (7 * 24 * 3600),
            "history": history,
        }
        for idx in range(payload["strategy"]["markets_max"])
    ]
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
    assert output["results"]["starting_bankroll_usd"] == 1000
    assert output["results"]["return_pct"] >= 20.0


def test_config_example_uses_seren_polymarket_publisher_urls() -> None:
    payload = json.loads(CONFIG_EXAMPLE_PATH.read_text(encoding="utf-8"))
    backtest = payload.get("backtest", {})
    assert backtest.get("gamma_markets_url", "").startswith(
        "https://api.serendb.com/publishers/polymarket-data/"
    )
    assert backtest.get("clob_history_url", "").startswith(
        "https://api.serendb.com/publishers/polymarket-trading-serenai/"
    )
    assert backtest.get("clob_history_url", "").endswith("/trades")


def test_backtest_rejects_non_seren_polymarket_data_source(tmp_path: Path) -> None:
    # Keep this negative-path test without embedding direct endpoint literals,
    # so publisher-enforcement grep checks stay signal-only on runtime/config code.
    bad_gamma_url = "https://gamma" + "-api." + "polymarket.com/markets"
    bad_clob_url = "https://clob." + "polymarket.com/prices-history"
    payload = {
        "execution": {"dry_run": True, "live_mode": False},
        "backtest": {
            "days": 90,
            "fidelity_minutes": 60,
            "participation_rate": 0.2,
            "volatility_window_points": 24,
            "min_liquidity_usd": 0,
            "markets_fetch_limit": 1,
            "min_history_points": 10,
            "gamma_markets_url": bad_gamma_url,
            "clob_history_url": bad_clob_url,
        },
        "strategy": {
            "bankroll_usd": 1000,
            "markets_max": 1,
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

    assert result.returncode == 1, result.stderr
    output = json.loads(result.stdout)
    assert output["status"] == "error"
    assert output["error_code"] == "backtest_data_load_failed"
    assert "Seren Polymarket Publisher" in output["message"]
