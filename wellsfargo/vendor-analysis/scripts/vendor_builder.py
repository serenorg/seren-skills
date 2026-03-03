"""Pure-logic vendor analysis builder (no DB dependencies)."""
from __future__ import annotations

import re
import statistics
from collections import defaultdict
from datetime import date
from typing import Any


def normalize_vendor(description: str) -> str:
    text = description.upper().strip()
    for prefix in ("POS ", "ACH ", "DEBIT ", "CREDIT ", "ONLINE ", "RECURRING ", "AUTOPAY "):
        if text.startswith(prefix):
            text = text[len(prefix):]
    text = re.sub(r"\s*[#]?\d{4,}$", "", text)
    text = re.sub(r"\s*REF:\S+$", "", text)
    text = re.sub(r"\s*\d{1,2}/\d{1,2}(/\d{2,4})?", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _parse_date(val: Any) -> date | None:
    if isinstance(val, date):
        return val
    if isinstance(val, str):
        try:
            return date.fromisoformat(val[:10])
        except (ValueError, IndexError):
            return None
    return None


def aggregate_vendors(transactions: list[dict[str, Any]], top_n: int = 50) -> dict[str, Any]:
    vendor_data: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"amounts": [], "dates": [], "categories": [], "txn_count": 0}
    )
    for txn in transactions:
        amount = float(txn.get("amount", 0))
        if amount >= 0:
            continue
        vendor = normalize_vendor(str(txn.get("description_raw", "")))
        if not vendor:
            continue
        vendor_data[vendor]["amounts"].append(abs(amount))
        d = _parse_date(txn.get("txn_date"))
        if d:
            vendor_data[vendor]["dates"].append(d)
        vendor_data[vendor]["categories"].append(str(txn.get("category", "uncategorized")))
        vendor_data[vendor]["txn_count"] += 1

    vendors: list[dict[str, Any]] = []
    for name, data in vendor_data.items():
        amounts = data["amounts"]
        dates = sorted(data["dates"])
        total_spend = round(sum(amounts), 2)
        avg_amount = round(statistics.mean(amounts), 2) if amounts else 0.0
        cat_counts: dict[str, int] = {}
        for c in data["categories"]:
            cat_counts[c] = cat_counts.get(c, 0) + 1
        primary_category = max(cat_counts, key=cat_counts.get) if cat_counts else "uncategorized"
        vendors.append({
            "vendor_normalized": name, "category": primary_category,
            "total_spend": total_spend, "txn_count": data["txn_count"],
            "avg_amount": avg_amount,
            "first_seen": dates[0].isoformat() if dates else "",
            "last_seen": dates[-1].isoformat() if dates else "",
        })

    vendors.sort(key=lambda v: -v["total_spend"])
    for i, v in enumerate(vendors):
        v["spend_rank"] = i + 1

    total_spend = round(sum(v["total_spend"] for v in vendors), 2)
    return {"vendors": vendors[:top_n], "unique_vendors": len(vendors), "total_spend": total_spend, "top_n": top_n}


def render_markdown(analysis: dict[str, Any], period_start: date, period_end: date, run_id: str, txn_count: int) -> str:
    lines = [
        "# Wells Fargo Vendor Analysis", "",
        f"**Period:** {period_start.isoformat()} to {period_end.isoformat()}",
        f"**Run ID:** {run_id}",
        f"**Transactions analyzed:** {txn_count}", "",
        "## Summary", "",
        "| Metric | Value |", "|--------|------:|",
        f"| Unique Vendors | {analysis['unique_vendors']} |",
        f"| Total Spend | ${analysis['total_spend']:,.2f} |", "",
    ]
    vendors = analysis.get("vendors", [])
    if vendors:
        lines.extend([
            f"## Top {len(vendors)} Vendors by Spend", "",
            "| Rank | Vendor | Category | Total Spend | Txns | Avg Amount |",
            "|-----:|--------|----------|----------:|-----:|---------:|",
        ])
        for v in vendors:
            lines.append(f"| {v['spend_rank']} | {v['vendor_normalized']} | {v['category']} | ${v['total_spend']:,.2f} | {v['txn_count']} | ${v['avg_amount']:,.2f} |")
        lines.append("")
    return "\n".join(lines) + "\n"
