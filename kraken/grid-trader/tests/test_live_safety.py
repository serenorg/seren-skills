from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


_SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
_MODULES_TO_CLEAR = (
    "adaptive_runtime",
    "agent",
    "grid_manager",
    "logger",
    "pair_selector",
    "position_tracker",
    "seren_client",
    "serendb_store",
)


def _load_local_module(module_name: str):
    script_dir = str(_SCRIPT_DIR)
    sys.path[:] = [script_dir, *[path for path in sys.path if path != script_dir]]
    for cached_name in _MODULES_TO_CLEAR:
        sys.modules.pop(cached_name, None)
    spec = importlib.util.spec_from_file_location(
        f"{Path(__file__).stem}_{module_name}",
        _SCRIPT_DIR / f"{module_name}.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


agent = _load_local_module("agent")
KrakenGridTrader = agent.KrakenGridTrader
LiveRiskError = agent.LiveRiskError


def _build_trader(tmp_path, monkeypatch) -> KrakenGridTrader:
    monkeypatch.chdir(tmp_path)
    trader = KrakenGridTrader.__new__(KrakenGridTrader)
    trader.config = {
        "trading_pair": "XBTUSD",
        "risk_management": {
            "min_quote_reserve_usd": 100.0,
            "max_live_drawdown_pct": 10.0,
        },
        "execution": {
            "cancel_on_error": True,
            "operation_timeout_seconds": 30,
            "cycle_timeout_seconds": 90,
        },
    }
    trader.is_dry_run = False
    trader.live_risk_state = {}
    trader.active_orders = {"abc": {"side": "buy"}}
    trader.running = True
    trader.session_id = "session"
    trader.store = None
    trader.tracker = type(
        "Tracker",
        (),
        {
            "btc_balance": 0.0,
            "remove_open_order": lambda *args, **kwargs: None,
        },
    )()
    trader.adaptive_store = type(
        "AdaptiveStore",
        (),
        {"state": {}, "save": lambda *args, **kwargs: None},
    )()
    trader.current_adaptive_decision = type(
        "Decision",
        (),
        {"accepted_params": {"max_open_orders": 40}},
    )()
    trader.logger = type("Logger", (), {"log_error": lambda *args, **kwargs: None})()
    trader._cycle_deadline_at = None
    trader._store_call = lambda context, fn: None
    return trader


def test_quote_reserve_skips_buy_orders(tmp_path, monkeypatch) -> None:
    trader = _build_trader(tmp_path, monkeypatch)
    placed: list[tuple[str, float, float]] = []
    trader._place_order = lambda pair, side, price, volume: (placed.append((side, price, volume)) or True)

    trader._place_grid_orders(
        {"buy": [{"price": 95.0, "volume": 10.0}, {"price": 10.0, "volume": 1.0}], "sell": []},
        {},
        1000.0,
    )

    assert placed == [("buy", 10.0, 1.0)]


def test_open_order_cap_blocks_new_orders(tmp_path, monkeypatch) -> None:
    trader = _build_trader(tmp_path, monkeypatch)
    trader.current_adaptive_decision.accepted_params = {"max_open_orders": 1}
    trader._place_order = lambda *args, **kwargs: pytest.fail("should not place new orders")

    summary = trader._place_grid_orders(
        {"buy": [{"price": 95.0, "volume": 0.1}], "sell": []},
        {"existing": {"descr": {"type": "buy", "price": "96.0"}, "vol": "0.1"}},
        1000.0,
    )

    assert summary["placed_buy"] == 0
    assert summary["skipped_buy"] == 1


def test_position_cap_blocks_incremental_buys(tmp_path, monkeypatch) -> None:
    trader = _build_trader(tmp_path, monkeypatch)
    trader.config["risk_management"]["max_position_size"] = 0.5
    placed: list[tuple[str, float, float]] = []
    trader._place_order = lambda pair, side, price, volume: (placed.append((side, price, volume)) or True)

    summary = trader._place_grid_orders(
        {"buy": [{"price": 95.0, "volume": 0.1}], "sell": []},
        {},
        1000.0,
        base_balance=0.45,
    )

    assert placed == []
    assert summary["placed_buy"] == 0
    assert summary["skipped_buy"] == 1


def test_drawdown_guard_persists_peak_and_blocks(tmp_path, monkeypatch) -> None:
    trader = _build_trader(tmp_path, monkeypatch)
    first = trader._enforce_live_risk(current_price=100.0, base_balance=1.0, usd_balance=100.0)
    assert first["peak_equity_usd"] == 200.0

    trader.live_risk_state = {"peak_equity_usd": 200.0}
    with pytest.raises(LiveRiskError):
        trader._enforce_live_risk(current_price=100.0, base_balance=0.0, usd_balance=150.0)


def test_halt_live_trading_cancels_and_clears_orders(tmp_path, monkeypatch) -> None:
    trader = _build_trader(tmp_path, monkeypatch)
    cancelled: list[str] = []

    class _Seren:
        def cancel_all_orders(self):
            cancelled.append("cancelled")
            return {"count": 1}

    trader.seren = _Seren()
    trader._call_with_timeout = lambda label, fn, timeout_seconds=None: fn()

    trader._halt_live_trading("trading_cycle_error", {"error_type": "TimeoutError", "error_message": "boom"})

    assert cancelled == ["cancelled"]
    assert trader.active_orders == {}
    assert trader.running is False


def test_start_requires_allow_live_flag() -> None:
    with pytest.raises(SystemExit, match="--allow-live"):
        agent._require_live_confirmation("start", False)


def _write_minimal_config(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "campaign_name": "test-grid",
                "trading_pair": "XBTUSD",
                "strategy": {
                    "bankroll": 1000,
                    "grid_levels": 4,
                    "grid_spacing_percent": 1.0,
                    "order_size_percent": 5.0,
                    "price_range": {"min": 80000, "max": 120000},
                    "scan_interval_seconds": 1,
                },
                "risk_management": {
                    "stop_loss_bankroll": 900,
                    "max_open_orders": 4,
                },
                "adaptive": {"enabled": True},
            }
        ),
        encoding="utf-8",
    )


def test_dry_run_allows_in_memory_adaptive_state_without_serendb(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "config.json"
    _write_minimal_config(config_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SEREN_API_KEY", "test-key")
    monkeypatch.setattr(agent, "SerenClient", lambda api_key: object())
    monkeypatch.setattr(
        agent,
        "_build_store_from_env",
        lambda: (_ for _ in ()).throw(RuntimeError("seren-mcp not found")),
    )

    trader = KrakenGridTrader(str(config_path), dry_run=True)

    assert trader.store is None
    assert trader.adaptive_store.persistence is None


def test_live_adaptive_runtime_still_requires_serendb(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "config.json"
    _write_minimal_config(config_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SEREN_API_KEY", "test-key")
    monkeypatch.setattr(agent, "SerenClient", lambda api_key: object())
    monkeypatch.setattr(
        agent,
        "_build_store_from_env",
        lambda: (_ for _ in ()).throw(RuntimeError("seren-mcp not found")),
    )

    with pytest.raises(ValueError, match="Adaptive runtime requires SerenDB persistence"):
        KrakenGridTrader(str(config_path), dry_run=False)


def test_dry_run_uses_market_snapshot_fallback_when_publisher_unavailable(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "config.json"
    _write_minimal_config(config_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SEREN_API_KEY", "test-key")

    class _Seren:
        def get_market_snapshot(self, pair):
            raise RuntimeError(f"publisher unavailable for {pair}")

    monkeypatch.setattr(agent, "SerenClient", lambda api_key: _Seren())
    monkeypatch.setattr(
        agent,
        "_build_store_from_env",
        lambda: (_ for _ in ()).throw(RuntimeError("seren-mcp not found")),
    )

    trader = KrakenGridTrader(str(config_path), dry_run=True)
    snapshot = trader._get_market_snapshot("XBTUSD")

    assert snapshot["market_data_source"] == "dry_run_fallback"
    assert snapshot["current_price"] == 100000.0
    assert snapshot["bid"] < snapshot["current_price"] < snapshot["ask"]
