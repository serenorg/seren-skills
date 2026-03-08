from __future__ import annotations

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
    spec = importlib.util.spec_from_file_location("paired_market_basis_maker_agent_test", SCRIPT_PATH)
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
    assert payload["skill"] == "paired-market-basis-maker"


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


def test_config_example_targets_promotional_backtest_return(monkeypatch) -> None:
    module = _load_agent_module()
    payload = json.loads(CONFIG_EXAMPLE_PATH.read_text(encoding="utf-8"))

    defaults = module.to_strategy_params({})
    backtest_defaults = module.to_backtest_params({})
    assert defaults.bankroll_usd == payload["strategy"]["bankroll_usd"] == 1000
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
    assert output["results"]["starting_bankroll_usd"] == 1000
    assert output["results"]["return_pct"] >= 20.0


def test_trade_mode_fetches_live_pairs_when_config_markets_is_empty(monkeypatch) -> None:
    module = _load_agent_module()
    now_ts = int(time.time())
    fetched_urls: list[str] = []

    def fake_http_get_json(url: str, timeout: int = 30):
        fetched_urls.append(url)
        return [
            {
                "id": "LIVE-PAIR-1A",
                "question": "Will event A resolve YES?",
                "events": [{"id": "EVENT-1"}],
                "clobTokenIds": ["TOKEN-1A"],
                "outcomePrices": ["0.62", "0.38"],
                "liquidity": 25000,
                "volume24hr": 15000,
                "rebate_bps": 2.3,
                "endDate": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ts + 86400)),
            },
            {
                "id": "LIVE-PAIR-1B",
                "question": "Will event A resolve NO?",
                "events": [{"id": "EVENT-1"}],
                "clobTokenIds": ["TOKEN-1B"],
                "outcomePrices": ["0.41", "0.59"],
                "liquidity": 23000,
                "volume24hr": 12000,
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
    assert result["pair_trades"][0]["market_id"] == "LIVE-PAIR-1A"
    assert result["pair_trades"][0]["pair_market_id"] == "LIVE-PAIR-1B"
    assert any("/publishers/polymarket-data/markets?" in url for url in fetched_urls)


def test_live_trade_mode_uses_live_pair_loader_and_executor(monkeypatch) -> None:
    module = _load_agent_module()
    load_calls: list[dict[str, object]] = []
    execute_calls: list[dict[str, object]] = []

    class FakeTrader:
        def __init__(self, *, skill_root: Path, client_name: str, timeout_seconds: float = 30.0) -> None:
            self.skill_root = skill_root
            self.client_name = client_name
            self.timeout_seconds = timeout_seconds

        def get_positions(self) -> list[dict[str, object]]:
            return [
                {"asset_id": "TOKEN-PAIR-1A", "size": 3.0},
                {"asset_id": "TOKEN-PAIR-1B", "size": 2.0},
            ]

    def fake_load_live_pair_markets(**kwargs):
        load_calls.append(kwargs)
        return [
            {
                "market_id": "LIVE-PAIR-1A",
                "pair_market_id": "LIVE-PAIR-1B",
                "question": "Primary leg",
                "pair_question": "Pair leg",
                "token_id": "TOKEN-PAIR-1A",
                "pair_token_id": "TOKEN-PAIR-1B",
                "mid_price": 0.62,
                "pair_mid_price": 0.41,
                "best_bid": 0.61,
                "best_ask": 0.63,
                "pair_best_bid": 0.4,
                "pair_best_ask": 0.42,
                "tick_size": "0.01",
                "pair_tick_size": "0.01",
                "neg_risk": False,
                "pair_neg_risk": False,
                "seconds_to_resolution": 86400,
                "rebate_bps": 2.3,
                "basis_volatility_bps": 80,
            }
        ]

    def fake_execute_pair_trades(*, trader, pair_trades, markets, execution_settings):
        execute_calls.append(
            {
                "client_name": trader.client_name,
                "pair_trades": pair_trades,
                "markets": markets,
                "poll_attempts": execution_settings.poll_attempts,
            }
        )
        return {
            "orders_submitted": [{"id": "PAIR-ORDER-1"}, {"id": "PAIR-ORDER-2"}],
            "open_order_ids": ["PAIR-ORDER-1", "PAIR-ORDER-2"],
            "updated_leg_exposure": {"LIVE-PAIR-1A": 7.5, "LIVE-PAIR-1B": -7.5},
        }

    monkeypatch.setattr(module, "DirectClobTrader", FakeTrader)
    monkeypatch.setattr(module, "load_live_pair_markets", fake_load_live_pair_markets)
    monkeypatch.setattr(module, "execute_pair_trades", fake_execute_pair_trades)

    result = module.run_trade(
        config={
            "execution": {
                "dry_run": False,
                "live_mode": True,
                "prefer_live_market_data": True,
                "poll_attempts": 4,
            },
            "backtest": {
                "min_history_points": 72,
                "min_liquidity_usd": 0,
                "markets_fetch_page_size": 10,
                "max_markets": 2,
                "history_interval": "max",
                "history_fidelity_minutes": 60,
            },
            "strategy": {
                "bankroll_usd": 1000,
                "pairs_max": 1,
                "min_seconds_to_resolution": 60,
                "min_edge_bps": 2.0,
                "maker_rebate_bps": 2.3,
                "expected_unwind_cost_bps": 1.5,
                "adverse_selection_bps": 1.1,
                "basis_entry_bps": 35,
                "basis_exit_bps": 10,
                "expected_convergence_ratio": 0.35,
                "base_pair_notional_usd": 550,
                "max_notional_per_pair_usd": 750,
                "max_total_notional_usd": 1600,
                "max_leg_notional_usd": 800,
            },
            "state": {"leg_exposure": {"CONFIG-PAIR-A": 1.0}},
            "markets": [
                {
                    "market_id": "CONFIG-PAIR-A",
                    "pair_market_id": "CONFIG-PAIR-B",
                    "mid_price": 0.51,
                    "pair_mid_price": 0.5,
                    "seconds_to_resolution": 1000,
                    "basis_volatility_bps": 5,
                }
            ],
        },
        markets_file=None,
        yes_live=True,
    )

    assert result["status"] == "ok"
    assert result["mode"] == "live"
    assert result["market_source"] == "live-seren-publisher"
    assert result["state"] == {"leg_exposure": {"LIVE-PAIR-1A": 7.5, "LIVE-PAIR-1B": -7.5}}
    assert result["strategy_summary"]["orders_submitted"] == 2
    assert result["strategy_summary"]["open_orders"] == 2
    assert load_calls and load_calls[0]["pairs_max"] == 1
    assert execute_calls and execute_calls[0]["client_name"] == "paired-market-basis-maker"
    assert execute_calls[0]["pair_trades"][0]["market_id"] == "LIVE-PAIR-1A"
