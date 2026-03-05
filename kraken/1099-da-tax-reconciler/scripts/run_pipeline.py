#!/usr/bin/env python3
"""Run the full 1099-DA reconciliation pipeline.

MCP-native version: generates SQL for persistence via mcp__seren-mcp__run_sql_transaction.
When running inside Seren Desktop, the agent executes the SQL through MCP tools
instead of requiring a SEREN_API_KEY in .env.

Can also be run standalone for local JSON output without persistence.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Dict

from common import load_records, stable_id, write_json
from cost_basis_resolver import resolve
from reconciliation_audit import audit, normalize_tax_rows
from serendb_store import build_persist_transaction


def _load_normalizer_module():
    module_path = Path(__file__).with_name("1099da_normalizer.py")
    spec = importlib.util.spec_from_file_location("normalizer_1099da", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load 1099da_normalizer.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["normalizer_1099da"] = module
    spec.loader.exec_module(module)
    return module


def main() -> None:
    parser = argparse.ArgumentParser(description="Run full crypto 1099-DA reconciliation pipeline")
    parser.add_argument("--input-1099da", required=True, help="1099-DA CSV/JSON/JSONL path")
    parser.add_argument("--input-tax", required=True, help="Tax software CSV/JSON/JSONL path")
    parser.add_argument("--output-dir", required=True, help="Output directory for JSON artifacts")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    normalizer = _load_normalizer_module()
    normalized = normalizer.normalize_rows(load_records(args.input_1099da))
    resolved = resolve(normalized)
    tax_rows = normalize_tax_rows(load_records(args.input_tax))
    summary, exceptions = audit(resolved_rows=resolved, tax_rows=tax_rows)

    run_id = stable_id([args.input_1099da, args.input_tax, len(normalized), len(tax_rows)])

    write_json(str(output_dir / "normalized_1099da.json"), {"count": len(normalized), "records": normalized})
    write_json(str(output_dir / "resolved_lots.json"), {"count": len(resolved), "records": resolved})
    write_json(
        str(output_dir / "reconciliation_audit.json"),
        {"summary": summary, "exceptions": exceptions, "tax_rows": tax_rows},
    )

    # Build MCP persistence SQL (agent executes via mcp__seren-mcp__run_sql_transaction)
    persist_sql = build_persist_transaction(
        run_id=run_id,
        normalized=normalized,
        resolved=resolved,
        exceptions=exceptions,
        summary=summary,
        input_1099da_path=args.input_1099da,
        input_tax_path=args.input_tax,
    )
    write_json(str(output_dir / "persist_sql.json"), persist_sql)

    pipeline_result: Dict[str, Any] = {
        "run_id": run_id,
        "summary": summary,
        "exceptions": exceptions,
        "persistence": {
            "method": "mcp_native",
            "sql_statements_count": len(persist_sql),
            "sql_file": str(output_dir / "persist_sql.json"),
            "tables": [
                "crypto_tax.reconciliation_runs",
                "crypto_tax.normalized_1099da",
                "crypto_tax.resolved_lots",
                "crypto_tax.reconciliation_exceptions",
            ],
            "counts": {
                "normalized_1099da": len(normalized),
                "resolved_lots": len(resolved),
                "reconciliation_exceptions": len(exceptions),
            },
        },
    }
    write_json(str(output_dir / "pipeline_result.json"), pipeline_result)
    print(json.dumps(pipeline_result, indent=2))


if __name__ == "__main__":
    main()
