"""Smoke tests for the net worth builder (no DB required)."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

# Allow importing from scripts/
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from networth_builder import (  # noqa: E402
    build_networth_summary,
    compute_monthly_totals,
    compute_running_balance,
    render_markdown,
)


def _make_txn(amount: float, txn_date: str = "2025-06-15", category: str = "uncategorized") -> dict:
    return {
        "row_hash": f"hash_{abs(hash((amount, txn_date, category)))}",
        "account_masked": "****1234",
        "txn_date": txn_date,
        "description_raw": f"Test txn {category}",
        "amount": amount,
        "currency": "USD",
        "category": category,
        "category_source": "test",
        "confidence": 1.0,
    }


class TestComputeMonthlyTotals:
    def test_groups_by_month_correctly(self) -> None:
        transactions = [
            _make_txn(5000.0, "2025-01-10"),
            _make_txn(-2000.0, "2025-01-20"),
            _make_txn(3000.0, "2025-02-05"),
            _make_txn(-1000.0, "2025-02-15"),
            _make_txn(1500.0, "2025-03-01"),
        ]
        monthly = compute_monthly_totals(transactions)

        assert len(monthly) == 3
        assert monthly[0]["month_start"] == "2025-01-01"
        assert monthly[0]["inflows"] == 5000.0
        assert monthly[0]["outflows"] == -2000.0
        assert monthly[0]["net"] == 3000.0
        assert monthly[0]["txn_count"] == 2

        assert monthly[1]["month_start"] == "2025-02-01"
        assert monthly[1]["inflows"] == 3000.0
        assert monthly[1]["outflows"] == -1000.0
        assert monthly[1]["net"] == 2000.0
        assert monthly[1]["txn_count"] == 2

        assert monthly[2]["month_start"] == "2025-03-01"
        assert monthly[2]["inflows"] == 1500.0
        assert monthly[2]["outflows"] == 0.0
        assert monthly[2]["net"] == 1500.0
        assert monthly[2]["txn_count"] == 1


class TestComputeRunningBalance:
    def test_calculates_cumulative_balance(self) -> None:
        monthly = [
            {"month_start": "2025-01-01", "inflows": 5000.0, "outflows": -2000.0, "net": 3000.0, "txn_count": 2},
            {"month_start": "2025-02-01", "inflows": 3000.0, "outflows": -1000.0, "net": 2000.0, "txn_count": 2},
            {"month_start": "2025-03-01", "inflows": 1500.0, "outflows": -500.0, "net": 1000.0, "txn_count": 1},
        ]
        result = compute_running_balance(monthly, starting_balance=0.0)

        assert result[0]["running_balance"] == 3000.0
        assert result[1]["running_balance"] == 5000.0
        assert result[2]["running_balance"] == 6000.0

    def test_with_nonzero_starting_balance(self) -> None:
        monthly = [
            {"month_start": "2025-01-01", "inflows": 1000.0, "outflows": -500.0, "net": 500.0, "txn_count": 2},
        ]
        result = compute_running_balance(monthly, starting_balance=10000.0)

        assert result[0]["running_balance"] == 10500.0


class TestBuildNetworthSummary:
    def test_with_mixed_transactions(self) -> None:
        transactions = [
            _make_txn(5000.0, "2025-01-10"),
            _make_txn(-2000.0, "2025-01-20"),
            _make_txn(3000.0, "2025-02-05"),
            _make_txn(-4000.0, "2025-02-15"),
        ]
        summary = build_networth_summary(transactions, starting_balance=0.0)

        assert summary["total_inflows"] == 8000.0
        assert summary["total_outflows"] == -6000.0
        assert summary["net_change"] == 2000.0
        assert summary["starting_balance"] == 0.0
        assert summary["ending_balance"] == 2000.0
        assert len(summary["monthly"]) == 2
        assert summary["monthly"][0]["running_balance"] == 3000.0
        assert summary["monthly"][1]["running_balance"] == 2000.0

    def test_with_empty_input(self) -> None:
        summary = build_networth_summary([], starting_balance=0.0)

        assert summary["total_inflows"] == 0.0
        assert summary["total_outflows"] == 0.0
        assert summary["net_change"] == 0.0
        assert summary["starting_balance"] == 0.0
        assert summary["ending_balance"] == 0.0
        assert len(summary["monthly"]) == 0

    def test_with_starting_balance(self) -> None:
        transactions = [
            _make_txn(1000.0, "2025-03-10"),
            _make_txn(-500.0, "2025-03-20"),
        ]
        summary = build_networth_summary(transactions, starting_balance=25000.0)

        assert summary["starting_balance"] == 25000.0
        assert summary["ending_balance"] == 25500.0
        assert summary["net_change"] == 500.0
        assert len(summary["monthly"]) == 1
        assert summary["monthly"][0]["running_balance"] == 25500.0


class TestRenderMarkdown:
    def test_produces_valid_output(self) -> None:
        transactions = [
            _make_txn(5000.0, "2025-01-10"),
            _make_txn(-2000.0, "2025-01-20"),
            _make_txn(3000.0, "2025-02-05"),
        ]
        summary = build_networth_summary(transactions, starting_balance=1000.0)
        md = render_markdown(
            summary,
            period_start=date(2025, 1, 1),
            period_end=date(2025, 12, 31),
            run_id="test-run-001",
            txn_count=3,
        )

        assert "# Wells Fargo Net Worth Report" in md
        assert "2025-01-01" in md
        assert "2025-12-31" in md
        assert "test-run-001" in md
        assert "Starting Balance" in md
        assert "Ending Balance" in md
        assert "Net Change" in md
        assert "Monthly Breakdown" in md
        assert "Net Worth Trend" in md
        assert "Inflows" in md
        assert "Outflows" in md
        assert "Running Balance" in md
