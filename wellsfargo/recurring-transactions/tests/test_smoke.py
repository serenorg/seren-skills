"""Smoke tests for the recurring transaction detector (no DB required)."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from datetime import date, timedelta

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from recurring_detector import (  # noqa: E402
    compute_summary,
    detect_recurring_patterns,
    normalize_payee,
    render_markdown,
)

FREQUENCY_RULES = json.loads(
    (Path(__file__).resolve().parent.parent / "config" / "frequency_rules.json").read_text()
)


def _make_monthly_txns(payee: str, amount: float, category: str, count: int = 6) -> list[dict]:
    """Generate monthly recurring transactions."""
    base = date(2025, 1, 15)
    txns = []
    for i in range(count):
        d = date(2025, 1 + i if 1 + i <= 12 else (1 + i) - 12, 15)
        txns.append({
            "row_hash": f"hash_{payee}_{i}",
            "account_masked": "****1234",
            "txn_date": d.isoformat(),
            "description_raw": f"POS {payee} #{1000 + i}",
            "amount": amount,
            "currency": "USD",
            "category": category,
            "category_source": "test",
            "confidence": 1.0,
        })
    return txns


def _make_weekly_txns(payee: str, amount: float, category: str, count: int = 12) -> list[dict]:
    """Generate weekly recurring transactions."""
    base = date(2025, 1, 6)
    txns = []
    for i in range(count):
        d = base + timedelta(weeks=i)
        txns.append({
            "row_hash": f"hash_{payee}_{i}",
            "account_masked": "****1234",
            "txn_date": d.isoformat(),
            "description_raw": f"RECURRING {payee}",
            "amount": amount,
            "currency": "USD",
            "category": category,
            "category_source": "test",
            "confidence": 1.0,
        })
    return txns


class TestNormalizePayee:
    def test_removes_pos_prefix(self) -> None:
        assert normalize_payee("POS NETFLIX") == "NETFLIX"

    def test_removes_trailing_numbers(self) -> None:
        assert normalize_payee("NETFLIX 123456") == "NETFLIX"

    def test_removes_ach_prefix(self) -> None:
        assert normalize_payee("ACH SPOTIFY USA") == "SPOTIFY USA"

    def test_collapses_whitespace(self) -> None:
        assert normalize_payee("  AMAZON   PRIME  ") == "AMAZON PRIME"

    def test_removes_ref(self) -> None:
        assert normalize_payee("COMCAST REF:ABC123") == "COMCAST"


class TestDetectRecurringPatterns:
    def test_detects_monthly_subscription(self) -> None:
        txns = _make_monthly_txns("NETFLIX", -15.99, "subscriptions", count=6)
        patterns = detect_recurring_patterns(txns, FREQUENCY_RULES, min_occurrences=3, min_confidence=0.3)
        assert len(patterns) >= 1
        netflix = [p for p in patterns if "NETFLIX" in p["payee_normalized"]]
        assert len(netflix) == 1
        assert netflix[0]["frequency"] == "monthly"
        assert netflix[0]["avg_amount"] == 15.99

    def test_detects_weekly_pattern(self) -> None:
        txns = _make_weekly_txns("PLANET FITNESS", -10.0, "healthcare", count=12)
        patterns = detect_recurring_patterns(txns, FREQUENCY_RULES, min_occurrences=3, min_confidence=0.3)
        assert len(patterns) >= 1
        gym = [p for p in patterns if "PLANET FITNESS" in p["payee_normalized"]]
        assert len(gym) == 1
        assert gym[0]["frequency"] == "weekly"

    def test_no_patterns_from_random_txns(self) -> None:
        txns = [
            {
                "row_hash": f"hash_random_{i}",
                "account_masked": "****1234",
                "txn_date": date(2025, 1, 1 + i).isoformat(),
                "description_raw": f"RANDOM MERCHANT {i}",
                "amount": -float(i * 17 + 3),
                "currency": "USD",
                "category": "shopping",
                "category_source": "test",
                "confidence": 1.0,
            }
            for i in range(10)
        ]
        patterns = detect_recurring_patterns(txns, FREQUENCY_RULES, min_occurrences=3, min_confidence=0.5)
        assert len(patterns) == 0

    def test_respects_min_occurrences(self) -> None:
        txns = _make_monthly_txns("HULU", -7.99, "subscriptions", count=2)
        patterns = detect_recurring_patterns(txns, FREQUENCY_RULES, min_occurrences=3, min_confidence=0.3)
        assert len(patterns) == 0

    def test_respects_min_confidence(self) -> None:
        # Create transactions with very variable amounts (low confidence)
        txns = []
        for i in range(6):
            d = date(2025, 1 + i, 15)
            txns.append({
                "row_hash": f"hash_var_{i}",
                "account_masked": "****1234",
                "txn_date": d.isoformat(),
                "description_raw": "VARIABLE MERCHANT",
                "amount": -(10.0 + i * 50),  # 10, 60, 110, 160, 210, 260
                "currency": "USD",
                "category": "shopping",
                "category_source": "test",
                "confidence": 1.0,
            })
        patterns = detect_recurring_patterns(txns, FREQUENCY_RULES, min_occurrences=3, min_confidence=0.9)
        assert len(patterns) == 0


class TestComputeSummary:
    def test_summary_with_patterns(self) -> None:
        patterns = [
            {
                "payee_normalized": "NETFLIX",
                "frequency": "monthly",
                "avg_amount": 15.99,
                "is_active": True,
            },
            {
                "payee_normalized": "GYM",
                "frequency": "monthly",
                "avg_amount": 49.99,
                "is_active": True,
            },
        ]
        summary = compute_summary(patterns)
        assert summary["patterns_found"] == 2
        assert summary["active_patterns"] == 2
        assert summary["total_monthly_committed"] == 65.98
        assert summary["total_annual_committed"] == 791.76

    def test_summary_empty(self) -> None:
        summary = compute_summary([])
        assert summary["patterns_found"] == 0
        assert summary["total_monthly_committed"] == 0.0


class TestRenderMarkdown:
    def test_render_produces_valid_markdown(self) -> None:
        patterns = [
            {
                "payee_normalized": "NETFLIX",
                "category": "subscriptions",
                "frequency": "monthly",
                "avg_amount": 15.99,
                "median_amount": 15.99,
                "occurrence_count": 6,
                "confidence": 0.95,
                "first_seen": "2025-01-15",
                "last_seen": "2025-06-15",
                "next_expected": "2025-07-15",
                "is_active": True,
            },
        ]
        summary = compute_summary(patterns)
        md = render_markdown(
            patterns, summary,
            period_start=date(2025, 1, 1),
            period_end=date(2025, 12, 31),
            run_id="test-run-001",
            txn_count=100,
        )
        assert "# Wells Fargo Recurring Transactions" in md
        assert "NETFLIX" in md
        assert "Monthly" in md
        assert "test-run-001" in md
        assert "Est. Monthly Committed" in md
