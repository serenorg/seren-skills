"""Pure-logic budget vs. actual builder (no DB dependencies)."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any


def load_budget_targets(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Budget targets not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def aggregate_actuals(
    transactions: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Sum actual spending by category (expenses only, using absolute amounts)."""
    actuals: dict[str, dict[str, Any]] = {}
    for txn in transactions:
        amount = float(txn.get("amount", 0))
        if amount >= 0:
            continue  # Skip income
        category = str(txn.get("category", "uncategorized")).lower().strip()
        if category not in actuals:
            actuals[category] = {"amount": 0.0, "txn_count": 0}
        actuals[category]["amount"] = round(actuals[category]["amount"] + abs(amount), 2)
        actuals[category]["txn_count"] += 1
    return actuals


def compare_budget(
    actuals: dict[str, dict[str, Any]],
    budget_targets: dict[str, Any],
    num_months: float = 1.0,
) -> dict[str, Any]:
    """Compare actual spending against budget targets.

    Args:
        actuals: category -> {amount, txn_count} from aggregate_actuals
        budget_targets: loaded budget_targets.json
        num_months: number of months in the period (for pro-rating monthly budgets)

    Returns a dict with categories list, totals, and summary.
    """
    targets = budget_targets.get("targets", {})
    categories: list[dict[str, Any]] = []

    all_cats = set(targets.keys()) | set(actuals.keys())

    for cat in sorted(all_cats):
        target_spec = targets.get(cat, {})
        label = target_spec.get("label", cat.replace("_", " ").title())
        monthly_limit = float(target_spec.get("monthly_limit", 0))
        budget_amount = round(monthly_limit * num_months, 2)

        actual_data = actuals.get(cat, {"amount": 0.0, "txn_count": 0})
        actual_amount = actual_data["amount"]
        txn_count = actual_data["txn_count"]

        variance = round(budget_amount - actual_amount, 2)
        utilization = round((actual_amount / budget_amount * 100), 2) if budget_amount > 0 else (
            100.0 if actual_amount > 0 else 0.0
        )
        is_over = actual_amount > budget_amount and budget_amount > 0

        categories.append({
            "category": cat,
            "label": label,
            "budget_amount": budget_amount,
            "actual_amount": actual_amount,
            "variance": variance,
            "utilization_pct": utilization,
            "txn_count": txn_count,
            "is_over_budget": is_over,
        })

    total_budget = round(sum(c["budget_amount"] for c in categories), 2)
    total_actual = round(sum(c["actual_amount"] for c in categories), 2)
    total_variance = round(total_budget - total_actual, 2)
    categories_over = sum(1 for c in categories if c["is_over_budget"])

    return {
        "categories": categories,
        "total_budget": total_budget,
        "total_actual": total_actual,
        "total_variance": total_variance,
        "categories_over": categories_over,
    }


def render_markdown(
    comparison: dict[str, Any],
    period_start: date,
    period_end: date,
    run_id: str,
    txn_count: int,
) -> str:
    lines: list[str] = []
    lines.append("# Wells Fargo Budget Tracker")
    lines.append("")
    lines.append(f"**Period:** {period_start.isoformat()} to {period_end.isoformat()}")
    lines.append(f"**Run ID:** {run_id}")
    lines.append(f"**Transactions analyzed:** {txn_count}")
    lines.append("")

    lines.append("## Summary")
    lines.append("")
    lines.append("| Metric | Amount |")
    lines.append("|--------|-------:|")
    lines.append(f"| Total Budget | ${comparison['total_budget']:,.2f} |")
    lines.append(f"| Total Actual | ${comparison['total_actual']:,.2f} |")

    tv = comparison["total_variance"]
    var_display = f"${tv:,.2f}" if tv >= 0 else f"(${abs(tv):,.2f})"
    lines.append(f"| Variance | {var_display} |")
    lines.append(f"| Categories Over Budget | {comparison['categories_over']} |")
    lines.append("")

    lines.append("## Budget vs. Actual by Category")
    lines.append("")
    lines.append("| Category | Budget | Actual | Variance | Utilization | Status |")
    lines.append("|----------|-------:|-------:|---------:|------------:|--------|")

    for cat in comparison["categories"]:
        if cat["budget_amount"] == 0 and cat["actual_amount"] == 0:
            continue
        v = cat["variance"]
        var_str = f"${v:,.2f}" if v >= 0 else f"(${abs(v):,.2f})"
        status = "OVER" if cat["is_over_budget"] else "OK"
        lines.append(
            f"| {cat['label']} "
            f"| ${cat['budget_amount']:,.2f} "
            f"| ${cat['actual_amount']:,.2f} "
            f"| {var_str} "
            f"| {cat['utilization_pct']:.0f}% "
            f"| {status} |"
        )
    lines.append("")

    over_cats = [c for c in comparison["categories"] if c["is_over_budget"]]
    if over_cats:
        lines.append("## Over Budget Categories")
        lines.append("")
        for cat in sorted(over_cats, key=lambda c: c["variance"]):
            lines.append(f"- **{cat['label']}**: ${cat['actual_amount']:,.2f} spent vs ${cat['budget_amount']:,.2f} budget ({cat['utilization_pct']:.0f}%)")
        lines.append("")

    return "\n".join(lines) + "\n"
