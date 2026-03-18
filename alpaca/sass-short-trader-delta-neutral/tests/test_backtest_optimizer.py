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


optimizer = _load_local_module("backtest_optimizer")


def test_optimize_scan_config_targets_100_bankroll() -> None:
    base_config = {
        "run_profile": "single",
        "learning_mode": "adaptive-paper",
        "hedge_ticker": "QQQ",
        "universe": ["ADBE", "CRM", "NOW"],
    }

    def _run_scan(candidate: dict) -> dict:
        hedge_boost = 3.0 - abs(float(candidate["hedge_ratio"]) - 0.5) * 4.0
        pnl_pct = (
            10.0
            + (candidate["max_names_orders"] * 4.5)
            - ((candidate["min_conviction"] - 55.0) * 0.12)
            + hedge_boost
        )
        bankroll = float(candidate["portfolio_notional_usd"])
        return {
            "selected": candidate["universe"][: candidate["max_names_orders"]],
            "sim": {
                "net_pnl_20d": bankroll * (pnl_pct / 100.0),
                "gross_exposure": bankroll,
            },
        }

    optimized = optimizer.optimize_scan_config(base_config=base_config, run_scan=_run_scan)

    assert optimized["summary"]["applied"] is True
    assert optimized["summary"]["target_met"] is True
    assert optimized["config"]["portfolio_notional_usd"] == 100.0
    assert optimized["summary"]["modeled_pnl_pct"] >= 25.0
