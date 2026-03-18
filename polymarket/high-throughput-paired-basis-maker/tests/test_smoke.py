from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path


FIXTURE_DIR = Path(__file__).parent / "fixtures"
SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "agent.py"
CONFIG_EXAMPLE_PATH = Path(__file__).resolve().parents[1] / "config.example.json"


def _read_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _load_agent_module() -> object:
    spec = importlib.util.spec_from_file_location("high_throughput_paired_basis_maker_agent_test", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _synthetic_pair_series(points: int = 420, start_ts: int | None = None) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    start = start_ts or (int(time.time()) - (points * 3600))
    primary: list[tuple[int, float]] = []
    pair: list[tuple[int, float]] = []
    for i in range(points):
        cycle = i % 4
        if cycle == 0:
            p1, p2 = 0.54, 0.46
        elif cycle == 1:
            p1, p2 = 0.53, 0.47
        elif cycle == 2:
            p1, p2 = 0.515, 0.485
        else:
            p1, p2 = 0.505, 0.495
        primary.append((start + (i * 3600), p1))
        pair.append((start + (i * 3600), p2))
    return primary, pair


def test_happy_path_fixture_is_successful() -> None:
    payload = _read_fixture("happy_path.json")
    assert payload["status"] == "ok"
    assert payload["skill"] == "high-throughput-paired-basis-maker"


def test_connector_failure_fixture_has_error_code() -> None:
    payload = _read_fixture("connector_failure.json")
    assert payload["status"] == "error"
    assert payload["error_code"] == "connector_failure"


def test_policy_violation_fixture_has_error_code() -> None:
    payload = _read_fixture("policy_violation.json")
    assert payload["status"] == "error"
    assert payload["error_code"] == "policy_violation"


def test_dry_run_fixture_blocks_live_execution() -> None:
    payload = _read_fixture("dry_run_guard.json")
    assert payload["dry_run"] is True
    assert payload["blocked_action"] == "live_execution"


def test_config_example_runs_stateful_backtest_and_reports_replay_metrics(monkeypatch) -> None:
    module = _load_agent_module()
    payload = json.loads(CONFIG_EXAMPLE_PATH.read_text(encoding="utf-8"))
    payload["backtest"]["min_events"] = 1

    defaults = module.to_strategy_params({})
    backtest_defaults = module.to_backtest_params({})
    assert defaults.bankroll_usd == payload["strategy"]["bankroll_usd"] == 1000
    assert backtest_defaults.bankroll_usd == payload["backtest"]["bankroll_usd"] == 100
    assert defaults.base_pair_notional_usd == payload["strategy"]["base_pair_notional_usd"]
    assert backtest_defaults.participation_rate == payload["backtest"]["participation_rate"]

    primary, pair = _synthetic_pair_series()
    synthetic_markets = [
        {
            "market_id": f"M{idx}",
            "pair_market_id": f"P{idx}",
            "end_ts": int(time.time()) + (5 * 24 * 3600),
            "rebate_bps": payload["strategy"]["maker_rebate_bps"],
            "history": primary,
            "pair_history": pair,
        }
        for idx in range(max(payload["strategy"]["pairs_max"], 8))
    ]

    monkeypatch.setattr(
        module,
        "_load_backtest_markets",
        lambda p, bt, start_ts, end_ts: (synthetic_markets, "synthetic"),
    )

    output = module.run_backtest(payload, None)
    assert output["status"] == "ok"
    assert output["results"]["starting_bankroll_usd"] == 100
    assert output["results"]["fill_events"] > 0
    assert output["backtest_summary"]["quoted_points"] > 0
    assert sum(output["backtest_summary"]["orderbook_modes"].values()) == len(synthetic_markets)
    assert output["results"]["return_pct"] >= -100.0
    assert output["pairs"][0]["orderbook_mode"] in output["backtest_summary"]["orderbook_modes"]


def test_trade_mode_fetches_live_pairs_when_config_markets_is_empty(monkeypatch) -> None:
    module = _load_agent_module()
    now_ts = int(time.time())
    fetched_urls: list[str] = []

    def fake_http_get_json(url: str, timeout: int = 30):
        fetched_urls.append(url)
        return [
            {
                "id": "LIVE-HT-1A",
                "question": "Will event HT A resolve YES?",
                "events": [{"id": "EVENT-HT-1"}],
                "clobTokenIds": ["TOKEN-HT-1A"],
                "outcomePrices": ["0.64", "0.36"],
                "liquidity": 26000,
                "volume24hr": 16000,
                "rebate_bps": 2.3,
                "endDate": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ts + 86400)),
            },
            {
                "id": "LIVE-HT-1B",
                "question": "Will event HT A resolve NO?",
                "events": [{"id": "EVENT-HT-1"}],
                "clobTokenIds": ["TOKEN-HT-1B"],
                "outcomePrices": ["0.39", "0.61"],
                "liquidity": 24000,
                "volume24hr": 13000,
                "rebate_bps": 2.3,
                "endDate": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ts + 86400)),
            },
        ]

    monkeypatch.setattr(module, "_http_get_json", fake_http_get_json)
    config = {
        "execution": {"dry_run": True, "live_mode": False},
        "backtest": {"min_liquidity_usd": 0, "markets_fetch_page_size": 10, "max_markets": 2},
        "strategy": {"pairs_max": 1, "min_seconds_to_resolution": 60},
        "markets": [],
    }

    result = module.run_trade(config=config, markets_file=None, yes_live=False)

    assert result["status"] == "ok"
    assert result["strategy_summary"]["pairs_considered"] == 1
    assert result["strategy_summary"]["pairs_quoted"] == 1
    assert result["pair_trades"][0]["market_id"] == "LIVE-HT-1A"
    assert result["pair_trades"][0]["pair_market_id"] == "LIVE-HT-1B"
    assert any("/publishers/polymarket-data/markets?" in url for url in fetched_urls)


def test_backtest_optimizer_selects_targeted_pair_subset_and_tuned_config(monkeypatch) -> None:
    module = _load_agent_module()
    now_ts = int(time.time())
    config = {
        "strategy": {"bankroll_usd": 1000, "pairs_max": 3, "basis_entry_bps": 35, "base_pair_notional_usd": 600},
        "backtest": {
            "bankroll_usd": 100,
            "days": 90,
            "min_events": 1,
            "telemetry_path": "",
            "optimization": {"target_return_pct": 25.0},
        },
    }
    markets = [
        {"market_id": "M0", "pair_market_id": "P0", "question": "A", "pair_question": "B", "history": [], "pair_history": [], "rebate_bps": 2.3},
        {"market_id": "M1", "pair_market_id": "P1", "question": "C", "pair_question": "D", "history": [], "pair_history": [], "rebate_bps": 2.3},
        {"market_id": "M2", "pair_market_id": "P2", "question": "E", "pair_question": "F", "history": [], "pair_history": [], "rebate_bps": 2.3},
    ]

    def fake_load_backtest_markets(p, bt, start_ts, end_ts):
        return markets, "synthetic"

    def fake_simulate_pair(market, p, bt, allocated_capital=0.0):
        aggressive = p.base_pair_notional_usd > 650 and p.basis_entry_bps < 30
        pnl = 140.0 if aggressive and market["market_id"] in {"M0", "M1"} else (8.0 if market["market_id"] in {"M0", "M1"} else -2.0)
        return {
            "market_id": market["market_id"],
            "pair_market_id": market["pair_market_id"],
            "considered_points": 10,
            "quoted_points": 6,
            "traded_points": 6,
            "skipped_points": 0,
            "fill_events": 1,
            "filled_notional_usd": 100.0,
            "pnl_usd": pnl,
            "event_pnls": [pnl],
            "orderbook_mode": "synthetic",
            "telemetry": [],
        }

    monkeypatch.setattr(module, "_load_backtest_markets", fake_load_backtest_markets)
    monkeypatch.setattr(module, "_simulate_pair", fake_simulate_pair)
    monkeypatch.setattr(module, "write_telemetry_records", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "_fetch_predictions_pair_signals", lambda backtest_params: {})

    output = module.run_backtest(config, None)

    assert output["status"] == "ok"
    assert output["results"]["return_pct"] >= 25.0
    assert output["optimization_summary"]["target_met"] is True
    assert output["config_updates"]["strategy"]["base_pair_notional_usd"] > 600
    assert [target["market_id"] for target in output["optimization_summary"]["target_pairs"]] == ["M0", "M1"]


def test_main_applies_backtest_config_updates_before_trade(monkeypatch, tmp_path: Path) -> None:
    module = _load_agent_module()
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"execution": {"require_positive_backtest": True}, "strategy": {"basis_entry_bps": 35}}), encoding="utf-8")
    seen: dict[str, float] = {}

    monkeypatch.setattr(
        module,
        "parse_args",
        lambda: argparse.Namespace(
            config=str(config_path),
            run_type="trade",
            markets_file=None,
            backtest_file=None,
            backtest_days=None,
            allow_negative_backtest=False,
            yes_live=False,
        ),
    )
    monkeypatch.setattr(module, "load_config", lambda path: json.loads(config_path.read_text(encoding="utf-8")))
    monkeypatch.setattr(
        module,
        "run_backtest",
        lambda config, backtest_days, backtest_file=None: {
            "status": "ok",
            "results": {"return_pct": 30.0},
            "config_updates": {"strategy": {"basis_entry_bps": 12.0}, "state": {"backtest_optimizer": {"target_met": True}}},
        },
    )
    def fake_run_trade(config, markets_file, yes_live):
        seen["basis_entry_bps"] = config["strategy"]["basis_entry_bps"]
        return {"status": "ok"}

    monkeypatch.setattr(module, "run_trade", fake_run_trade)

    assert module.main() == 0
    assert seen["basis_entry_bps"] == 12.0
    persisted = json.loads(config_path.read_text(encoding="utf-8"))
    assert persisted["strategy"]["basis_entry_bps"] == 12.0
