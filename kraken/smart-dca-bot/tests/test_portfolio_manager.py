from __future__ import annotations

import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from portfolio_manager import PortfolioManager


def test_normalize_allocations_sums_to_one() -> None:
    pm = PortfolioManager()
    normalized = pm.normalize_allocations({"XBTUSD": 60, "ETHUSD": 40})
    assert round(sum(normalized.values()), 8) == 1.0


def test_detect_drift_marks_biggest_delta_first() -> None:
    pm = PortfolioManager()
    drifts = pm.detect_drift(
        targets={"XBTUSD": 0.6, "ETHUSD": 0.4},
        current={"XBTUSD": 0.5, "ETHUSD": 0.5},
    )
    assert drifts[0].asset in {"XBTUSD", "ETHUSD"}
    assert abs(drifts[0].drift_pct) >= abs(drifts[1].drift_pct)


def test_build_plan_rebalances_underweight_assets() -> None:
    pm = PortfolioManager()
    plan = pm.build_dca_buy_plan(
        total_dca_amount_usd=200,
        targets={"XBTUSD": 0.6, "ETHUSD": 0.4},
        current={"XBTUSD": 0.45, "ETHUSD": 0.55},
        rebalance_threshold_pct=5.0,
    )
    assert plan["mode"] == "drift_rebalance"
    assert plan["orders"]
    xbt_order = [row for row in plan["orders"] if row["asset"] == "XBTUSD"]
    assert xbt_order
