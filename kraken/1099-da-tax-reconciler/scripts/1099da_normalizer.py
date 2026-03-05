#!/usr/bin/env python3
"""Normalize 1099-DA rows into canonical JSON records."""

from __future__ import annotations

import argparse
from typing import Any, Dict, List

from common import find_value, load_records, parse_dt, stable_id, to_float, write_json


def normalize_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for idx, row in enumerate(rows):
        asset = find_value(row, "asset")
        quantity = to_float(find_value(row, "quantity"))
        disposed_at = parse_dt(find_value(row, "disposed_at"))
        acquired_at = parse_dt(find_value(row, "acquired_at"))
        proceeds = to_float(find_value(row, "proceeds_usd"))
        basis = to_float(find_value(row, "cost_basis_usd"))
        gain = to_float(find_value(row, "gain_loss_usd"))
        fee = to_float(find_value(row, "fee_usd"))
        holding_period = find_value(row, "holding_period")
        broker = find_value(row, "broker")
        tx_hash = find_value(row, "tx_hash")

        if basis is None and proceeds is not None and gain is not None:
            basis = proceeds - gain
        if gain is None and proceeds is not None and basis is not None:
            gain = proceeds - basis

        record = {
            "record_id": stable_id([asset, quantity, disposed_at, proceeds, basis, idx]),
            "asset": asset,
            "quantity": quantity,
            "disposed_at": disposed_at,
            "acquired_at": acquired_at,
            "proceeds_usd": proceeds,
            "cost_basis_usd": basis,
            "gain_loss_usd": gain,
            "fee_usd": fee,
            "holding_period": holding_period,
            "broker": broker,
            "tx_hash": tx_hash,
            "raw": row,
        }
        records.append(record)
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize 1099-DA records")
    parser.add_argument("--input", required=True, help="Input CSV/JSON/JSONL path")
    parser.add_argument("--output", required=True, help="Output JSON path")
    args = parser.parse_args()

    rows = load_records(args.input)
    normalized = normalize_rows(rows)
    write_json(args.output, {"count": len(normalized), "records": normalized})
    print(f"Normalized {len(normalized)} records -> {args.output}")


if __name__ == "__main__":
    main()
