from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


_SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"


def _load_local_module(module_name: str):
    script_dir = str(_SCRIPT_DIR)
    sys.path[:] = [script_dir, *[path for path in sys.path if path != script_dir]]
    spec = importlib.util.spec_from_file_location(
        f"{Path(__file__).stem}_{module_name}",
        _SCRIPT_DIR / f"{module_name}.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


grid_manager = _load_local_module("grid_manager")


def test_optimize_backtest_configuration_targets_100_bankroll() -> None:
    config = {
        "pairs": ["XBTUSD", "ETHUSD"],
        "strategy": {
            "bankroll": 1000.0,
            "grid_levels": 20,
            "grid_spacing_percent": 2.0,
            "order_size_percent": 5.0,
            "price_range": {"min": 45000, "max": 55000},
            "scan_interval_seconds": 60,
        },
        "risk_management": {
            "stop_loss_bankroll": 800.0,
            "max_open_orders": 40,
        },
    }

    optimized = grid_manager.optimize_backtest_configuration(config)

    assert optimized["summary"]["applied"] is True
    assert optimized["summary"]["target_met"] is True
    assert optimized["summary"]["bankroll_usd"] == 100.0
    assert optimized["config"]["strategy"]["bankroll"] == 100.0
    assert optimized["config"]["risk_management"]["stop_loss_bankroll"] == 80.0
