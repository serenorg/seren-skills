from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys


_SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
_MODULES_TO_CLEAR = (
    "agent",
    "backtest_optimizer",
    "dca_engine",
    "logger",
    "optimizer",
    "portfolio_manager",
    "position_tracker",
    "scanner",
    "seren_api_client",
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
agent.ensure_seren_api_key = lambda config: os.getenv("SEREN_API_KEY", "sb_local_test")
run_once = agent.run_once


def _write_config(path: Path, mode: str) -> None:
    base = {
        "dry_run": True,
        "inputs": {
            "mode": mode,
            "asset": "BTC-USD",
            "dca_amount_usd": 50.0,
            "total_dca_amount_usd": 200.0,
            "frequency": "weekly",
            "dca_window_hours": 24,
            "execution_strategy": "vwap_optimized",
            "risk_level": "moderate",
            "use_usdc_routing": True,
            "auto_stake_enabled": False,
            "include_coinbase_earn": True,
        },
        "portfolio": {
            "allocations": {
                "BTC-USD": 0.6,
                "ETH-USD": 0.25,
                "SOL-USD": 0.1,
                "DOT-USD": 0.05,
            },
            "rebalance_threshold_pct": 5.0,
            "sell_to_rebalance": False,
        },
        "scanner": {
            "enabled": True,
            "max_reallocation_pct": 20.0,
            "min_24h_volume_usd": 1_000_000,
            "min_market_cap_usd": 100_000_000,
            "require_coinbase_verified": True,
            "scan_interval_hours": 6,
            "signals": ["oversold_rsi", "volume_spike", "mean_reversion", "new_listing", "learn_earn"],
            "require_approval": True,
            "approval_action": "pending",
        },
        "risk": {
            "max_daily_spend_usd": 500.0,
            "max_notional_usd": 5000.0,
            "max_slippage_bps": 150,
        },
        "runtime": {
            "mock_market_data": True,
            "market_scan_assets": ["BTC-USD", "ETH-USD", "SOL-USD", "DOT-USD", "AVAX-USD"],
            "loop_interval_seconds": 60,
            "cancel_pending_on_shutdown": True,
            "cancel_on_error": True,
            "api_timeout_seconds": 30,
            "run_timeout_seconds": 90,
            "min_cash_reserve_usd": 0.0,
            "max_live_drawdown_pct": 0.0,
        },
        "seren": {
            "auto_register_key": True,
            "api_base_url": "https://api.serendb.com",
        },
    }
    path.write_text(json.dumps(base), encoding="utf-8")


def test_single_asset_run_once_ok(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SEREN_API_KEY", "sb_local_test")
    config = tmp_path / "config.json"
    _write_config(config, "single_asset")

    result = run_once(
        config_path=str(config),
        allow_live=False,
        accept_risk_disclaimer=True,
    )
    assert result["status"] == "ok"
    assert result["mode"] == "single_asset"


def test_run_once_persists_100_bankroll_backtest_selection(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SEREN_API_KEY", "sb_local_test")
    config = tmp_path / "config.json"
    _write_config(config, "single_asset")

    result = run_once(
        config_path=str(config),
        allow_live=False,
        accept_risk_disclaimer=True,
    )

    updated = json.loads(config.read_text(encoding="utf-8"))
    assert result["optimization"]["bankroll_usd"] == 100.0
    assert result["optimization"]["target_met"] is True
    assert updated["inputs"]["total_dca_amount_usd"] == 100.0
    assert updated["risk"]["max_notional_usd"] >= 100.0


def test_first_run_requires_explicit_disclaimer_acceptance(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SEREN_API_KEY", "sb_local_test")
    config = tmp_path / "config.json"
    _write_config(config, "single_asset")

    result = run_once(
        config_path=str(config),
        allow_live=False,
        accept_risk_disclaimer=False,
    )
    assert result["status"] == "error"
    assert result["error_code"] == "policy_violation"


def test_portfolio_mode_run_once_ok(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SEREN_API_KEY", "sb_local_test")
    config = tmp_path / "config.json"
    _write_config(config, "portfolio")

    result = run_once(
        config_path=str(config),
        allow_live=False,
        accept_risk_disclaimer=True,
    )
    assert result["status"] == "ok"
    assert result["mode"] == "portfolio"


def test_opportunity_scanner_mode_run_once_ok(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SEREN_API_KEY", "sb_local_test")
    config = tmp_path / "config.json"
    _write_config(config, "opportunity_scanner")

    result = run_once(
        config_path=str(config),
        allow_live=False,
        accept_risk_disclaimer=True,
    )
    assert result["status"] == "ok"
    assert result["mode"] == "opportunity_scanner"
    assert isinstance(result["payload"].get("signals", []), list)


def test_legacy_scanner_mode_is_rejected(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SEREN_API_KEY", "sb_local_test")
    config = tmp_path / "config.json"
    _write_config(config, "scanner")

    result = run_once(
        config_path=str(config),
        allow_live=False,
        accept_risk_disclaimer=True,
    )
    assert result["status"] == "error"
    assert result["error_code"] == "validation_error"


def test_live_mode_requires_explicit_flags(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SEREN_API_KEY", "sb_local_test")
    config = tmp_path / "config.json"
    _write_config(config, "single_asset")

    body = json.loads(config.read_text(encoding="utf-8"))
    body["dry_run"] = False
    body["runtime"]["mock_market_data"] = True
    config.write_text(json.dumps(body), encoding="utf-8")

    result = run_once(
        config_path=str(config),
        allow_live=False,
        accept_risk_disclaimer=True,
    )
    assert result["status"] == "error"


def test_live_mode_blocks_when_cash_reserve_would_be_breached(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SEREN_API_KEY", "sb_local_test")
    config = tmp_path / "config.json"
    _write_config(config, "single_asset")

    body = json.loads(config.read_text(encoding="utf-8"))
    body["dry_run"] = False
    body["runtime"]["mock_market_data"] = False
    body["runtime"]["min_cash_reserve_usd"] = 120.0
    config.write_text(json.dumps(body), encoding="utf-8")

    class _Client:
        def get_balance(self):
            return {"USD": 150.0}

    monkeypatch.setattr(agent, "build_coinbase_client", lambda config: _Client())

    result = run_once(
        config_path=str(config),
        allow_live=True,
        accept_risk_disclaimer=True,
    )

    assert result["status"] == "error"
    assert result["error_code"] == "live_safety_error"


def test_live_mode_cancels_known_orders_on_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SEREN_API_KEY", "sb_local_test")
    config = tmp_path / "config.json"
    _write_config(config, "single_asset")

    body = json.loads(config.read_text(encoding="utf-8"))
    body["dry_run"] = False
    body["runtime"]["mock_market_data"] = False
    config.write_text(json.dumps(body), encoding="utf-8")

    cancelled: list[str] = []

    class _Client:
        def get_balance(self):
            return {"USD": 500.0}

        def cancel_order(self, order_id: str):
            cancelled.append(order_id)
            return {"success": True}

    def _boom(*, execution_context=None, **kwargs):
        execution_context.setdefault("order_ids", []).append("order-1")
        raise agent.CoinbaseAPIError("boom")

    monkeypatch.setattr(agent, "build_coinbase_client", lambda config: _Client())
    monkeypatch.setattr(agent, "_single_asset_mode", _boom)

    result = run_once(
        config_path=str(config),
        allow_live=True,
        accept_risk_disclaimer=True,
    )

    assert result["status"] == "error"
    assert cancelled == ["order-1"]
    assert result["cancelled_on_error"][0]["order_id"] == "order-1"


def test_stop_trading_cancels_pending_orders_from_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "config.json"
    _write_config(config, "single_asset")

    body = json.loads(config.read_text(encoding="utf-8"))
    body["dry_run"] = False
    config.write_text(json.dumps(body), encoding="utf-8")

    state_path = tmp_path / "state" / "last_state_export.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"pending_order_ids": ["order-1", "order-2"]}), encoding="utf-8")
    monkeypatch.setattr(agent, "STATE_EXPORT_PATH", state_path)

    cancelled: list[str] = []

    class _Client:
        def cancel_order(self, order_id: str):
            cancelled.append(order_id)
            return {"success": True}

    monkeypatch.setattr(agent, "build_coinbase_client", lambda config: _Client())

    result = agent.stop_trading(config_path=str(config))

    assert "stop trading" in result["message"]
    assert cancelled == ["order-1", "order-2"]


def test_stop_trading_reports_missing_coinbase_credentials(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "config.json"
    _write_config(config, "single_asset")

    body = json.loads(config.read_text(encoding="utf-8"))
    body["dry_run"] = False
    config.write_text(json.dumps(body), encoding="utf-8")

    result = agent.stop_trading(config_path=str(config))

    assert result["status"] == "error"
    assert "missing" in result["message"]
