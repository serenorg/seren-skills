"""Smoke tests for the budget tracker builder (no DB required)."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from datetime import date

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from budget_builder import aggregate_actuals, compare_budget, render_markdown  # noqa: E402

BUDGET_TARGETS = json.loads(
    (Path(__file__).resolve().parent.parent / "config" / "budget_targets.json").read_text()
)


def _make_txn(amount: float, category: str = "uncategorized") -> dict:
    return {
        "row_hash": f"hash_{abs(hash((amount, category)))}",
        "account_masked": "****1234",
        "txn_date": "2025-06-15",
        "description_raw": f"Test txn {category}",
        "amount": amount,
        "currency": "USD",
        "category": category,
        "category_source": "test",
        "confidence": 1.0,
    }


class TestAggregateActuals:
    def test_sums_expenses_by_category(self) -> None:
        txns = [
            _make_txn(-200.0, "groceries"),
            _make_txn(-150.0, "groceries"),
            _make_txn(-80.0, "dining"),
        ]
        actuals = aggregate_actuals(txns)
        assert actuals["groceries"]["amount"] == 350.0
        assert actuals["groceries"]["txn_count"] == 2
        assert actuals["dining"]["amount"] == 80.0

    def test_ignores_income(self) -> None:
        txns = [
            _make_txn(5000.0, "payroll"),
            _make_txn(-100.0, "groceries"),
        ]
        actuals = aggregate_actuals(txns)
        assert "payroll" not in actuals
        assert actuals["groceries"]["amount"] == 100.0

    def test_empty_transactions(self) -> None:
        actuals = aggregate_actuals([])
        assert len(actuals) == 0


class TestCompareBudget:
    def test_under_budget(self) -> None:
        actuals = {"groceries": {"amount": 300.0, "txn_count": 5}}
        result = compare_budget(actuals, BUDGET_TARGETS, num_months=1.0)
        grocery_cat = [c for c in result["categories"] if c["category"] == "groceries"]
        assert len(grocery_cat) == 1
        assert grocery_cat[0]["budget_amount"] == 600.0
        assert grocery_cat[0]["actual_amount"] == 300.0
        assert grocery_cat[0]["variance"] == 300.0
        assert grocery_cat[0]["is_over_budget"] is False

    def test_over_budget(self) -> None:
        actuals = {"dining": {"amount": 500.0, "txn_count": 10}}
        result = compare_budget(actuals, BUDGET_TARGETS, num_months=1.0)
        dining_cat = [c for c in result["categories"] if c["category"] == "dining"]
        assert len(dining_cat) == 1
        assert dining_cat[0]["is_over_budget"] is True
        assert dining_cat[0]["variance"] == -100.0
        assert result["categories_over"] >= 1

    def test_multi_month_pro_rates(self) -> None:
        actuals = {"groceries": {"amount": 1000.0, "txn_count": 20}}
        result = compare_budget(actuals, BUDGET_TARGETS, num_months=3.0)
        grocery_cat = [c for c in result["categories"] if c["category"] == "groceries"]
        assert grocery_cat[0]["budget_amount"] == 1800.0
        assert grocery_cat[0]["is_over_budget"] is False

    def test_empty_actuals(self) -> None:
        result = compare_budget({}, BUDGET_TARGETS, num_months=1.0)
        assert result["total_actual"] == 0.0
        assert result["categories_over"] == 0

    def test_uncategorized_spending_tracked(self) -> None:
        actuals = {"mystery_cat": {"amount": 50.0, "txn_count": 1}}
        result = compare_budget(actuals, BUDGET_TARGETS, num_months=1.0)
        mystery = [c for c in result["categories"] if c["category"] == "mystery_cat"]
        assert len(mystery) == 1
        assert mystery[0]["actual_amount"] == 50.0


class TestRenderMarkdown:
    def test_render_produces_valid_markdown(self) -> None:
        actuals = {"groceries": {"amount": 700.0, "txn_count": 15}, "dining": {"amount": 200.0, "txn_count": 8}}
        comparison = compare_budget(actuals, BUDGET_TARGETS, num_months=1.0)
        md = render_markdown(comparison, period_start=date(2025, 6, 1), period_end=date(2025, 6, 30), run_id="test-run-001", txn_count=23)
        assert "# Wells Fargo Budget Tracker" in md
        assert "test-run-001" in md
        assert "Budget" in md
        assert "Actual" in md
        assert "Variance" in md
        assert "Groceries" in md

    def test_over_budget_section_appears(self) -> None:
        actuals = {"groceries": {"amount": 800.0, "txn_count": 20}}
        comparison = compare_budget(actuals, BUDGET_TARGETS, num_months=1.0)
        md = render_markdown(comparison, period_start=date(2025, 6, 1), period_end=date(2025, 6, 30), run_id="test-run-002", txn_count=20)
        assert "Over Budget Categories" in md
        assert "OVER" in md
