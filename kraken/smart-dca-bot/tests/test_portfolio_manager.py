from __future__ import annotations

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


def test_current_allocations_reads_kraken_prefixed_balances() -> None:
    pm = PortfolioManager()
    allocations = pm.current_allocations(
        balances={"XXBT": 1.0, "XETH": 10.0},
        prices={"XBTUSD": 10_000.0, "ETHUSD": 2_000.0},
        targets={"XBTUSD": 0.5, "ETHUSD": 0.5},
    )
    assert allocations["ETHUSD"] > allocations["XBTUSD"]
