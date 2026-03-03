"""Pure-logic recurring transaction detector (no DB dependencies)."""
from __future__ import annotations

import json
import re
import statistics
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any


def load_frequency_rules(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Frequency rules not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_payee(description: str) -> str:
    """Normalize a transaction description to a canonical payee name."""
    text = description.upper().strip()
    # Remove common prefixes
    for prefix in ("POS ", "ACH ", "DEBIT ", "CREDIT ", "ONLINE ", "RECURRING ", "AUTOPAY "):
        if text.startswith(prefix):
            text = text[len(prefix):]
    # Remove trailing reference numbers (#123, REF:456, etc.)
    text = re.sub(r"\s*[#]?\d{4,}$", "", text)
    text = re.sub(r"\s*REF:\S+$", "", text)
    # Remove date patterns
    text = re.sub(r"\s*\d{1,2}/\d{1,2}(/\d{2,4})?", "", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _detect_frequency(
    dates: list[date],
    frequency_rules: dict[str, Any],
) -> tuple[str, float]:
    """Determine the most likely frequency from a sorted list of dates.

    Returns (frequency_name, regularity_score).
    """
    if len(dates) < 2:
        return "unknown", 0.0

    intervals = []
    for i in range(1, len(dates)):
        intervals.append((dates[i] - dates[i - 1]).days)

    if not intervals:
        return "unknown", 0.0

    median_interval = statistics.median(intervals)
    rules = frequency_rules.get("frequencies", {})

    best_freq = "irregular"
    best_score = 0.0

    for freq_name, rule in rules.items():
        lo = rule["interval_days_min"]
        hi = rule["interval_days_max"]
        expected = rule["expected_interval"]

        if lo <= median_interval <= hi:
            # Score based on how consistent intervals are relative to expected
            deviations = [abs(iv - expected) / expected for iv in intervals]
            avg_dev = statistics.mean(deviations)
            score = max(0.0, 1.0 - avg_dev)
            if score > best_score:
                best_freq = freq_name
                best_score = score

    return best_freq, round(best_score, 4)


def detect_recurring_patterns(
    transactions: list[dict[str, Any]],
    frequency_rules: dict[str, Any],
    min_occurrences: int = 3,
    amount_tolerance_pct: float = 0.15,
    min_confidence: float = 0.5,
) -> list[dict[str, Any]]:
    """Detect recurring transaction patterns from a list of transactions.

    Groups transactions by normalized payee and amount similarity,
    then checks for frequency regularity.
    """
    # Group by normalized payee
    payee_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for txn in transactions:
        payee = normalize_payee(str(txn.get("description_raw", "")))
        if payee:
            payee_groups[payee].append(txn)

    patterns: list[dict[str, Any]] = []

    for payee, txns in payee_groups.items():
        if len(txns) < min_occurrences:
            continue

        # Sub-group by similar amounts
        amount_groups = _group_by_amount(txns, amount_tolerance_pct)

        for amount_group in amount_groups:
            if len(amount_group) < min_occurrences:
                continue

            amounts = [abs(float(t.get("amount", 0))) for t in amount_group]
            dates = sorted(
                [_parse_date(t.get("txn_date", "")) for t in amount_group if _parse_date(t.get("txn_date", ""))]
            )

            if len(dates) < min_occurrences:
                continue

            freq_name, regularity = _detect_frequency(dates, frequency_rules)
            if freq_name in ("unknown", "irregular"):
                continue

            avg_amount = round(statistics.mean(amounts), 2)
            median_amount = round(statistics.median(amounts), 2)

            # Confidence = regularity * amount_consistency
            amount_std = statistics.stdev(amounts) if len(amounts) > 1 else 0.0
            amount_consistency = max(0.0, 1.0 - (amount_std / avg_amount)) if avg_amount > 0 else 0.0
            confidence = round(regularity * amount_consistency, 4)

            if confidence < min_confidence:
                continue

            category = str(amount_group[0].get("category", "uncategorized"))
            last_seen = dates[-1]
            expected_interval = frequency_rules.get("frequencies", {}).get(freq_name, {}).get("expected_interval", 30)
            next_expected = last_seen + timedelta(days=expected_interval)

            patterns.append({
                "payee_normalized": payee,
                "category": category,
                "frequency": freq_name,
                "avg_amount": avg_amount,
                "median_amount": median_amount,
                "occurrence_count": len(amount_group),
                "confidence": confidence,
                "first_seen": dates[0].isoformat(),
                "last_seen": last_seen.isoformat(),
                "next_expected": next_expected.isoformat(),
                "is_active": (date.today() - last_seen).days < expected_interval * 2,
            })

    # Sort by average amount descending
    patterns.sort(key=lambda p: -p["avg_amount"])
    return patterns


def _group_by_amount(
    txns: list[dict[str, Any]],
    tolerance_pct: float,
) -> list[list[dict[str, Any]]]:
    """Group transactions by similar absolute amounts."""
    groups: list[list[dict[str, Any]]] = []
    remaining = list(txns)

    while remaining:
        seed = remaining.pop(0)
        seed_amount = abs(float(seed.get("amount", 0)))
        group = [seed]
        new_remaining = []

        for txn in remaining:
            txn_amount = abs(float(txn.get("amount", 0)))
            if seed_amount == 0:
                if txn_amount == 0:
                    group.append(txn)
                else:
                    new_remaining.append(txn)
            elif abs(txn_amount - seed_amount) / seed_amount <= tolerance_pct:
                group.append(txn)
            else:
                new_remaining.append(txn)

        remaining = new_remaining
        groups.append(group)

    return groups


def _parse_date(val: Any) -> date | None:
    if isinstance(val, date):
        return val
    if isinstance(val, str):
        try:
            return date.fromisoformat(val[:10])
        except (ValueError, IndexError):
            return None
    return None


def compute_summary(patterns: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute summary statistics from detected patterns."""
    monthly_rates = {
        "weekly": 4.33,
        "biweekly": 2.17,
        "monthly": 1.0,
        "quarterly": 1 / 3,
        "annual": 1 / 12,
    }

    total_monthly = 0.0
    active_count = 0
    for p in patterns:
        rate = monthly_rates.get(p["frequency"], 1.0)
        total_monthly += p["avg_amount"] * rate
        if p.get("is_active", False):
            active_count += 1

    return {
        "patterns_found": len(patterns),
        "active_patterns": active_count,
        "total_monthly_committed": round(total_monthly, 2),
        "total_annual_committed": round(total_monthly * 12, 2),
    }


def render_markdown(
    patterns: list[dict[str, Any]],
    summary: dict[str, Any],
    period_start: date,
    period_end: date,
    run_id: str,
    txn_count: int,
) -> str:
    lines: list[str] = []
    lines.append("# Wells Fargo Recurring Transactions")
    lines.append("")
    lines.append(f"**Period:** {period_start.isoformat()} to {period_end.isoformat()}")
    lines.append(f"**Run ID:** {run_id}")
    lines.append(f"**Transactions analyzed:** {txn_count}")
    lines.append(f"**Recurring patterns found:** {summary['patterns_found']}")
    lines.append(f"**Active patterns:** {summary['active_patterns']}")
    lines.append("")

    lines.append("## Summary")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|------:|")
    lines.append(f"| Est. Monthly Committed | ${summary['total_monthly_committed']:,.2f} |")
    lines.append(f"| Est. Annual Committed | ${summary['total_annual_committed']:,.2f} |")
    lines.append(f"| Patterns Detected | {summary['patterns_found']} |")
    lines.append(f"| Active Patterns | {summary['active_patterns']} |")
    lines.append("")

    if patterns:
        lines.append("## Detected Recurring Transactions")
        lines.append("")
        lines.append("| Payee | Frequency | Avg Amount | Occurrences | Confidence | Last Seen | Next Expected | Active |")
        lines.append("|-------|-----------|----------:|------------:|----------:|-----------|---------------|--------|")
        for p in patterns:
            active_str = "Yes" if p.get("is_active") else "No"
            lines.append(
                f"| {p['payee_normalized']} "
                f"| {p['frequency'].title()} "
                f"| ${p['avg_amount']:,.2f} "
                f"| {p['occurrence_count']} "
                f"| {p['confidence']:.1%} "
                f"| {p['last_seen']} "
                f"| {p['next_expected']} "
                f"| {active_str} |"
            )
        lines.append("")

    return "\n".join(lines) + "\n"
