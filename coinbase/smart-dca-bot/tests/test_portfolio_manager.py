from __future__ import annotations

from portfolio_manager import PortfolioManager


def test_normalize_allocations_sums_to_one() -> None:
    pm = PortfolioManager()
    normalized = pm.normalize_allocations({"BTC-USD": 60, "ETH-USD": 40})
    assert round(sum(normalized.values()), 8) == 1.0


def test_detect_drift_marks_biggest_delta_first() -> None:
    pm = PortfolioManager()
    drifts = pm.detect_drift(
        targets={"BTC-USD": 0.6, "ETH-USD": 0.4},
        current={"BTC-USD": 0.5, "ETH-USD": 0.5},
    )
    assert drifts[0].asset in {"BTC-USD", "ETH-USD"}
    assert abs(drifts[0].drift_pct) >= abs(drifts[1].drift_pct)


def test_build_plan_rebalances_underweight_assets() -> None:
    pm = PortfolioManager()
    plan = pm.build_dca_buy_plan(
        total_dca_amount_usd=200,
        targets={"BTC-USD": 0.6, "ETH-USD": 0.4},
        current={"BTC-USD": 0.45, "ETH-USD": 0.55},
        rebalance_threshold_pct=5.0,
    )
    assert plan["mode"] == "drift_rebalance"
    assert plan["orders"]
    xbt_order = [row for row in plan["orders"] if row["asset"] == "BTC-USD"]
    assert xbt_order


def test_current_allocations_reads_coinbase_balances() -> None:
    pm = PortfolioManager()
    allocations = pm.current_allocations(
        balances={"BTC": 1.0, "ETH": 10.0},
        prices={"BTC-USD": 10_000.0, "ETH-USD": 2_000.0},
        targets={"BTC-USD": 0.5, "ETH-USD": 0.5},
    )
    assert allocations["ETH-USD"] > allocations["BTC-USD"]
