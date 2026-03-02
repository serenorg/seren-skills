from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from common import dump_json, ensure_dir


def _category_counts(categories: list[dict[str, Any]]) -> dict[str, int]:
    counter = Counter(row.get("category", "uncategorized") for row in categories)
    return dict(sorted(counter.items(), key=lambda item: (-item[1], item[0])))


def _totals(transactions: list[dict[str, Any]]) -> dict[str, float]:
    debit = 0.0
    credit = 0.0
    for txn in transactions:
        amount = float(txn.get("amount", 0.0))
        if amount < 0:
            debit += amount
        else:
            credit += amount
    return {"debit_total": round(debit, 2), "credit_total": round(credit, 2)}


def write_report(
    out_dir: Path,
    run_record: dict[str, Any],
    statement_files: list[dict[str, Any]],
    transactions: list[dict[str, Any]],
    categories: list[dict[str, Any]],
) -> dict[str, Any]:
    report_dir = ensure_dir(out_dir / "reports")
    export_dir = ensure_dir(out_dir / "exports")

    accounts = sorted({row.get("account_masked", "****") for row in statement_files})
    report_payload = {
        "run_id": run_record["run_id"],
        "status": run_record["status"],
        "mode": run_record["mode"],
        "started_at": run_record["started_at"],
        "ended_at": run_record["ended_at"],
        "selector_profile_version": run_record.get("selector_profile_version"),
        "pdf_count": len(statement_files),
        "transaction_count": len(transactions),
        "categorized_count": len(categories),
        "accounts": accounts,
        "totals": _totals(transactions),
        "category_counts": _category_counts(categories),
    }

    json_path = report_dir / f"{run_record['run_id']}.json"
    md_path = report_dir / f"{run_record['run_id']}.md"
    dump_json(json_path, report_payload)

    lines = [
        f"# Wells Fargo Run {run_record['run_id']}",
        "",
        f"- Status: {run_record['status']}",
        f"- Mode: {run_record['mode']}",
        f"- PDFs downloaded: {len(statement_files)}",
        f"- Transactions parsed: {len(transactions)}",
        f"- Transactions categorized: {len(categories)}",
        f"- Accounts: {', '.join(accounts) if accounts else 'none'}",
        "",
        "## Totals",
        "",
        f"- Debit total: {report_payload['totals']['debit_total']}",
        f"- Credit total: {report_payload['totals']['credit_total']}",
        "",
        "## Category Counts",
        "",
    ]

    for category, count in report_payload["category_counts"].items():
        lines.append(f"- {category}: {count}")

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    export_path = export_dir / f"{run_record['run_id']}.transactions.jsonl"
    with export_path.open("w", encoding="utf-8") as f:
        for row in transactions:
            import json

            f.write(json.dumps(row, sort_keys=True) + "\n")

    return {
        "report_json": str(json_path.resolve()),
        "report_md": str(md_path.resolve()),
        "transactions_jsonl": str(export_path.resolve()),
        "payload": report_payload,
    }
