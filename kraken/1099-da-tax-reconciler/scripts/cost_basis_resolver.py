#!/usr/bin/env python3
"""Resolve missing cost basis fields and infer holding periods."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from common import parse_dt, to_float, write_json


def _days_between(start_iso: str, end_iso: str) -> int:
    start = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
    end = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
    return (end - start).days


def resolve(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    resolved_records: List[Dict[str, Any]] = []

    for row in records:
        proceeds = to_float(row.get("proceeds_usd"))
        basis = to_float(row.get("cost_basis_usd"))
        gain = to_float(row.get("gain_loss_usd"))
        method = "as_provided"

        if basis is None and proceeds is not None and gain is not None:
            basis = proceeds - gain
            method = "derived_from_proceeds_minus_gain"
        elif gain is None and proceeds is not None and basis is not None:
            gain = proceeds - basis
            method = "derived_from_proceeds_minus_basis"

        acquired_at = parse_dt(row.get("acquired_at"))
        disposed_at = parse_dt(row.get("disposed_at"))
        holding_period = row.get("holding_period")
        if not holding_period and acquired_at and disposed_at:
            days = _days_between(acquired_at, disposed_at)
            holding_period = "long-term" if days > 365 else "short-term"

        resolved_records.append(
            {
                **row,
                "acquired_at": acquired_at,
                "disposed_at": disposed_at,
                "cost_basis_usd": basis,
                "gain_loss_usd": gain,
                "holding_period": holding_period,
                "basis_resolution_method": method,
            }
        )

    return resolved_records


def main() -> None:
    parser = argparse.ArgumentParser(description="Resolve cost basis and holding periods")
    parser.add_argument("--input", required=True, help="Input normalized JSON path")
    parser.add_argument("--output", required=True, help="Output JSON path")
    args = parser.parse_args()

    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    records = payload.get("records", payload)
    if not isinstance(records, list):
        raise ValueError("Input must contain a list in 'records' or be a top-level array")

    resolved = resolve(records)
    write_json(args.output, {"count": len(resolved), "records": resolved})
    print(f"Resolved {len(resolved)} records -> {args.output}")


if __name__ == "__main__":
    main()
