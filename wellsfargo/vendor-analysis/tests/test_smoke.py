"""Smoke tests for the vendor analysis builder (no DB required)."""
from __future__ import annotations

import sys
from pathlib import Path
from datetime import date

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from vendor_builder import aggregate_vendors, normalize_vendor, render_markdown


def _make_txn(amount: float, description: str, category: str = "shopping", txn_date: str = "2025-06-15") -> dict:
    return {
        "row_hash": f"hash_{abs(hash((amount, description)))}",
        "account_masked": "****1234",
        "txn_date": txn_date,
        "description_raw": description,
        "amount": amount,
        "currency": "USD",
        "category": category,
        "category_source": "test",
        "confidence": 1.0,
    }


class TestNormalizeVendor:
    def test_removes_pos_prefix(self) -> None:
        assert normalize_vendor("POS AMAZON") == "AMAZON"

    def test_removes_trailing_numbers(self) -> None:
        assert normalize_vendor("NETFLIX 123456") == "NETFLIX"

    def test_collapses_whitespace(self) -> None:
        assert normalize_vendor("  TARGET   STORE  ") == "TARGET STORE"


class TestAggregateVendors:
    def test_groups_by_vendor(self) -> None:
        txns = [
            _make_txn(-50.0, "POS AMAZON 12345", "shopping", "2025-01-15"),
            _make_txn(-30.0, "POS AMAZON 67890", "shopping", "2025-02-15"),
            _make_txn(-100.0, "NETFLIX 11111", "subscriptions", "2025-03-15"),
        ]
        result = aggregate_vendors(txns)
        assert result["unique_vendors"] == 2
        amazon = [v for v in result["vendors"] if v["vendor_normalized"] == "AMAZON"]
        assert len(amazon) == 1
        assert amazon[0]["total_spend"] == 80.0
        assert amazon[0]["txn_count"] == 2

    def test_ignores_income(self) -> None:
        txns = [
            _make_txn(5000.0, "ACH PAYROLL", "payroll"),
            _make_txn(-50.0, "POS STARBUCKS", "dining"),
        ]
        result = aggregate_vendors(txns)
        assert result["unique_vendors"] == 1

    def test_empty_transactions(self) -> None:
        result = aggregate_vendors([])
        assert result["unique_vendors"] == 0
        assert result["total_spend"] == 0.0

    def test_ranks_by_spend(self) -> None:
        txns = [
            _make_txn(-500.0, "RENT LLC", "housing"),
            _make_txn(-50.0, "COFFEE SHOP", "dining"),
            _make_txn(-200.0, "GROCERY MART", "groceries"),
        ]
        result = aggregate_vendors(txns)
        assert result["vendors"][0]["spend_rank"] == 1
        assert result["vendors"][0]["vendor_normalized"] == "RENT LLC"

    def test_top_n_limits(self) -> None:
        txns = [_make_txn(-10.0, f"VENDOR {i}", "shopping") for i in range(10)]
        result = aggregate_vendors(txns, top_n=3)
        assert len(result["vendors"]) == 3
        assert result["unique_vendors"] == 10


class TestRenderMarkdown:
    def test_render_produces_valid_markdown(self) -> None:
        txns = [_make_txn(-500.0, "RENT LLC", "housing"), _make_txn(-50.0, "POS STARBUCKS", "dining")]
        analysis = aggregate_vendors(txns)
        md = render_markdown(analysis, date(2025, 1, 1), date(2025, 12, 31), "test-run-001", 2)
        assert "# Wells Fargo Vendor Analysis" in md
        assert "RENT LLC" in md
        assert "Total Spend" in md
