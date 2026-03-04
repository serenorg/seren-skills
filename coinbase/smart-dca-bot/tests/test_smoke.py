from __future__ import annotations

import json
import os
from pathlib import Path

from agent import run_once


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
