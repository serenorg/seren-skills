"""Pure-logic net worth builder (no DB dependencies)."""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any


def compute_monthly_totals(transactions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group transactions by month and compute inflows, outflows, net, and txn_count.

    Returns a sorted list of dicts:
        {month_start, inflows, outflows, net, txn_count}
    """
    buckets: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"inflows": 0.0, "outflows": 0.0, "txn_count": 0}
    )

    for txn in transactions:
        txn_date = txn.get("txn_date")
        if isinstance(txn_date, str):
            txn_date = date.fromisoformat(txn_date)
        month_key = txn_date.replace(day=1).isoformat()
        amount = float(txn.get("amount", 0))
        if amount >= 0:
            buckets[month_key]["inflows"] = round(buckets[month_key]["inflows"] + amount, 2)
        else:
            buckets[month_key]["outflows"] = round(buckets[month_key]["outflows"] + amount, 2)
        buckets[month_key]["txn_count"] += 1

    result: list[dict[str, Any]] = []
    for month_key in sorted(buckets):
        b = buckets[month_key]
        net = round(b["inflows"] + b["outflows"], 2)
        result.append(
            {
                "month_start": month_key,
                "inflows": b["inflows"],
                "outflows": b["outflows"],
                "net": net,
                "txn_count": b["txn_count"],
            }
        )
    return result


def compute_running_balance(
    monthly_totals: list[dict[str, Any]],
    starting_balance: float = 0.0,
) -> list[dict[str, Any]]:
    """Add a running_balance field to each month dict (cumulative from starting_balance)."""
    balance = starting_balance
    for month in monthly_totals:
        balance = round(balance + month["net"], 2)
        month["running_balance"] = balance
    return monthly_totals


def build_networth_summary(
    transactions: list[dict[str, Any]],
    starting_balance: float = 0.0,
) -> dict[str, Any]:
    """Build a complete net worth summary from transactions.

    Returns a dict with:
        monthly: list of monthly dicts (with running_balance)
        total_inflows, total_outflows, net_change, ending_balance, starting_balance
    """
    monthly = compute_monthly_totals(transactions)
    monthly = compute_running_balance(monthly, starting_balance)

    total_inflows = round(sum(m["inflows"] for m in monthly), 2)
    total_outflows = round(sum(m["outflows"] for m in monthly), 2)
    net_change = round(total_inflows + total_outflows, 2)
    ending_balance = monthly[-1]["running_balance"] if monthly else starting_balance

    return {
        "monthly": monthly,
        "total_inflows": total_inflows,
        "total_outflows": total_outflows,
        "net_change": net_change,
        "starting_balance": starting_balance,
        "ending_balance": ending_balance,
    }


def render_markdown(
    summary: dict[str, Any],
    period_start: date,
    period_end: date,
    run_id: str,
    txn_count: int,
) -> str:
    """Render a net worth report as Markdown."""
    lines: list[str] = []
    lines.append("# Wells Fargo Net Worth Report")
    lines.append("")
    lines.append(f"**Period:** {period_start.isoformat()} to {period_end.isoformat()}")
    lines.append(f"**Run ID:** {run_id}")
    lines.append(f"**Transactions analyzed:** {txn_count}")
    lines.append(f"**Starting Balance:** ${summary['starting_balance']:,.2f}")
    lines.append("")

    lines.append("## Monthly Breakdown")
    lines.append("")
    lines.append("| Month | Inflows | Outflows | Net | Running Balance | Txns |")
    lines.append("|-------|--------:|---------:|----:|----------------:|-----:|")
    for m in summary["monthly"]:
        lines.append(
            f"| {m['month_start']} "
            f"| ${m['inflows']:,.2f} "
            f"| ${m['outflows']:,.2f} "
            f"| ${m['net']:,.2f} "
            f"| ${m['running_balance']:,.2f} "
            f"| {m['txn_count']} |"
        )
    lines.append("")

    lines.append("## Summary")
    lines.append("")
    lines.append("| | Amount |")
    lines.append("|--|-------:|")
    lines.append(f"| Starting Balance | ${summary['starting_balance']:,.2f} |")
    lines.append(f"| Total Inflows | ${summary['total_inflows']:,.2f} |")
    lines.append(f"| Total Outflows | ${summary['total_outflows']:,.2f} |")
    lines.append(f"| Net Change | ${summary['net_change']:,.2f} |")
    lines.append(f"| **Ending Balance** | **${summary['ending_balance']:,.2f}** |")
    lines.append("")

    lines.append("## Net Worth Trend")
    lines.append("")
    if summary["monthly"]:
        max_balance = max(m["running_balance"] for m in summary["monthly"])
        min_balance = min(m["running_balance"] for m in summary["monthly"])
        lines.append(f"- **Peak:** ${max_balance:,.2f}")
        lines.append(f"- **Trough:** ${min_balance:,.2f}")
        lines.append(f"- **Final:** ${summary['ending_balance']:,.2f}")
    else:
        lines.append("No data for the selected period.")
    lines.append("")

    return "\n".join(lines)
