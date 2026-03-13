from __future__ import annotations

import importlib.util
import json
import math
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


def _build_history_and_orderbooks(
    now_ts: int,
    points: int = 240,
) -> tuple[list[dict[str, float]], list[dict[str, float]]]:
    start_ts = now_ts - (points * 3600)
    history: list[dict[str, float]] = []
    orderbooks: list[dict[str, float]] = []
    for i in range(points):
        px = max(0.05, min(0.95, 0.5 + (0.012 * math.sin(i / 5.0)) + (0.003 * math.cos(i / 11.0))))
        ts = start_ts + (i * 3600)
        history.append({"t": ts, "p": round(px, 6)})
        orderbooks.append(
            {
                "t": ts,
                "best_bid": round(px - 0.0015, 6),
                "best_ask": round(px + 0.0015, 6),
                "bid_size_usd": 250.0,
                "ask_size_usd": 250.0,
            }
        )
    return history, orderbooks


def _base_backtest_payload(now_ts: int, telemetry_path: Path) -> dict:
    history, orderbooks = _build_history_and_orderbooks(now_ts)
    return {
        "execution": {"dry_run": True, "live_mode": False},
        "backtest": {
            "days": 90,
            "fidelity_minutes": 60,
            "participation_rate": 0.25,
            "volatility_window_points": 24,
            "min_history_points": 120,
            "min_liquidity_usd": 0,
            "require_orderbook_history": True,
            "spread_decay_bps": 45,
            "join_best_queue_factor": 0.85,
            "off_best_queue_factor": 0.35,
            "telemetry_path": str(telemetry_path),
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
            "max_spread_bps": 60,
            "volatility_spread_multiplier": 0.0,
            "base_order_notional_usd": 40,
            "max_notional_per_market_usd": 120,
            "max_total_notional_usd": 120,
            "max_position_notional_usd": 90,
            "inventory_skew_strength_bps": 25,
        },
        "backtest_markets": [
            {
                "market_id": "TEST-STATEFUL",
                "question": "Synthetic stateful market",
                "token_id": "TEST-STATEFUL",
                "rebate_bps": 3,
                "end_ts": now_ts + (14 * 24 * 3600),
                "history": history,
                "orderbooks": orderbooks,
            }
        ],
    }


def _run_backtest(tmp_path: Path, payload: dict) -> dict:
    tmp_path.mkdir(parents=True, exist_ok=True)
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
    assert result.stdout, result.stderr
    output = json.loads(result.stdout)
    assert result.returncode == (0 if output["status"] == "ok" else 1), result.stderr
    return output


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


def test_backtest_run_type_returns_stateful_result_and_telemetry(tmp_path: Path) -> None:
    now_ts = int(time.time())
    telemetry_path = tmp_path / "telemetry.jsonl"
    payload = _base_backtest_payload(now_ts, telemetry_path)

    output = _run_backtest(tmp_path, payload)

    assert output["status"] == "ok"
    assert output["mode"] == "backtest"
    assert output["backtest_summary"]["source"] == "config"
    assert output["backtest_summary"]["markets_selected"] == 1
    assert output["backtest_summary"]["orderbook_mode"] == "historical"
    assert output["results"]["events"] > 0
    assert output["results"]["telemetry_path"] == str(telemetry_path)
    telemetry_lines = telemetry_path.read_text(encoding="utf-8").strip().splitlines()
    assert telemetry_lines
    first = json.loads(telemetry_lines[0])
    assert first["market_id"] == "TEST-STATEFUL"
    assert "fill_fraction" in first or first["status"] == "skipped"


def test_stateful_backtest_enforces_risk_caps_in_replay(tmp_path: Path) -> None:
    now_ts = int(time.time())
    telemetry_path = tmp_path / "risk-caps.jsonl"
    payload = _base_backtest_payload(now_ts, telemetry_path)
    payload["strategy"].update(
        {
            "base_order_notional_usd": 200,
            "max_notional_per_market_usd": 60,
            "max_total_notional_usd": 60,
            "max_position_notional_usd": 40,
        }
    )

    output = _run_backtest(tmp_path, payload)

    assert output["status"] == "ok"
    records = [json.loads(line) for line in telemetry_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    quoted = [record for record in records if record.get("status") == "quoted"]
    assert quoted
    assert all((record["bid_notional_usd"] + record["ask_notional_usd"]) <= 60.0001 for record in quoted)
    assert all(
        abs(record.get("inventory_notional_after_usd", 0.0)) <= 40.0001
        for record in records
        if "inventory_notional_after_usd" in record
    )


def test_spread_decay_reduces_filled_notional_when_spread_widens(tmp_path: Path) -> None:
    now_ts = int(time.time())
    narrow_payload = _base_backtest_payload(now_ts, tmp_path / "narrow.jsonl")
    narrow_payload["strategy"].update(
        {"min_spread_bps": 20, "max_spread_bps": 20, "volatility_spread_multiplier": 0.0}
    )
    wide_payload = _base_backtest_payload(now_ts, tmp_path / "wide.jsonl")
    wide_payload["strategy"].update(
        {"min_spread_bps": 120, "max_spread_bps": 120, "volatility_spread_multiplier": 0.0}
    )

    narrow_output = _run_backtest(tmp_path / "narrow", narrow_payload)
    wide_output = _run_backtest(tmp_path / "wide", wide_payload)

    assert narrow_output["status"] == "ok"
    assert wide_output["status"] == "ok"
    assert wide_output["results"]["filled_notional_usd"] < narrow_output["results"]["filled_notional_usd"]


def test_backtest_requires_orderbook_history_when_configured(tmp_path: Path) -> None:
    now_ts = int(time.time())
    telemetry_path = tmp_path / "missing-books.jsonl"
    payload = _base_backtest_payload(now_ts, telemetry_path)
    payload["backtest_markets"][0].pop("orderbooks")

    output = _run_backtest(tmp_path, payload)

    assert output["status"] == "error"
    assert output["error_code"] == "backtest_data_load_failed"
    assert "historical order-book snapshots" in output["message"]


def test_config_example_uses_seren_polymarket_publisher_urls() -> None:
    payload = json.loads(CONFIG_EXAMPLE_PATH.read_text(encoding="utf-8"))
    backtest = payload.get("backtest", {})
    assert backtest.get("gamma_markets_url", "").startswith(
        "https://api.serendb.com/publishers/polymarket-data/"
    )
    assert backtest.get("clob_history_url", "").startswith(
        "https://clob.polymarket.com/"
    )
    assert backtest.get("clob_history_url", "").endswith("/prices-history")


def test_deprecated_history_publisher_url_is_canonicalized_to_direct_clob() -> None:
    agent = _load_agent_module()

    deprecated = "https://api.serendb.com/publishers/polymarket-trading-serenai/prices-history"
    assert agent._canonicalize_history_url(deprecated) == "https://clob.polymarket.com/prices-history"
    assert agent._canonicalize_history_url(f"{deprecated}?market=abc&interval=max") == (
        "https://clob.polymarket.com/prices-history?market=abc&interval=max"
    )


def test_backtest_rejects_non_seren_polymarket_data_source(tmp_path: Path) -> None:
    bad_gamma_url = "https://gamma" + "-api." + "polymarket.com/markets"
    bad_clob_url = "https://evil." + "example.com/prices-history"
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


def test_live_quote_mode_uses_live_market_loader_and_executor(monkeypatch) -> None:
    agent = _load_agent_module()
    load_calls: list[dict[str, object]] = []
    execute_calls: list[dict[str, object]] = []

    class FakeTrader:
        def __init__(self, *, skill_root: Path, client_name: str, timeout_seconds: float = 30.0) -> None:
            self.skill_root = skill_root
            self.client_name = client_name
            self.timeout_seconds = timeout_seconds

        def get_positions(self) -> list[dict[str, object]]:
            return [{"asset_id": "TOKEN-LIVE-1", "size": 4.0}]

    def fake_load_live_single_markets(**kwargs):
        load_calls.append(kwargs)
        return [
            {
                "market_id": "LIVE-MKT-1",
                "question": "Will live event happen?",
                "token_id": "TOKEN-LIVE-1",
                "mid_price": 0.48,
                "best_bid": 0.47,
                "best_ask": 0.49,
                "seconds_to_resolution": 86400,
                "volatility_bps": 50,
                "rebate_bps": 2.5,
                "tick_size": "0.01",
                "neg_risk": False,
            }
        ]

    def fake_execute_single_market_quotes(*, trader, quotes, markets, execution_settings):
        execute_calls.append(
            {
                "client_name": trader.client_name,
                "quotes": quotes,
                "markets": markets,
                "poll_attempts": execution_settings.poll_attempts,
            }
        )
        return {
            "orders_submitted": [{"id": "ORDER-1"}, {"id": "ORDER-2"}],
            "open_order_ids": ["ORDER-1"],
            "updated_inventory": {"LIVE-MKT-1": 12.5},
        }

    monkeypatch.setattr(agent, "DirectClobTrader", FakeTrader)
    monkeypatch.setattr(agent, "load_live_single_markets", fake_load_live_single_markets)
    monkeypatch.setattr(agent, "execute_single_market_quotes", fake_execute_single_market_quotes)

    result = agent.run_once(
        config={
            "execution": {
                "dry_run": False,
                "live_mode": True,
                "prefer_live_market_data": True,
                "poll_attempts": 3,
            },
            "backtest": {
                "volatility_window_points": 24,
                "min_liquidity_usd": 0,
                "markets_fetch_limit": 5,
                "fidelity_minutes": 60,
            },
            "strategy": {
                "bankroll_usd": 1000,
                "markets_max": 1,
                "min_seconds_to_resolution": 60,
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
            "state": {"inventory": {"CONFIG-MKT": 1.0}},
        },
        markets=[
            {
                "market_id": "CONFIG-MKT",
                "mid_price": 0.2,
                "best_bid": 0.19,
                "best_ask": 0.21,
                "seconds_to_resolution": 1000,
                "volatility_bps": 10,
            }
        ],
        yes_live=True,
    )

    assert result["status"] == "ok"
    assert result["mode"] == "live"
    assert result["market_source"] == "live-seren-publisher"
    assert result["state"] == {"inventory": {"LIVE-MKT-1": 12.5}}
    assert result["strategy_summary"]["orders_submitted"] == 2
    assert result["strategy_summary"]["open_orders"] == 1
    assert load_calls and load_calls[0]["markets_max"] == 1
    assert execute_calls and execute_calls[0]["client_name"] == "polymarket-maker-rebate-bot"
    assert execute_calls[0]["quotes"][0]["market_id"] == "LIVE-MKT-1"


def test_persist_runtime_state_updates_config_file(tmp_path: Path) -> None:
    agent = _load_agent_module()
    config_path = tmp_path / "config.json"
    config = {"state": {"inventory": {"OLD": 1.0}}}
    config_path.write_text(json.dumps(config), encoding="utf-8")

    agent._persist_runtime_state(
        str(config_path),
        config,
        {"inventory": {"LIVE-MKT-1": 12.5}},
    )

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["state"]["inventory"] == {"LIVE-MKT-1": 12.5}
