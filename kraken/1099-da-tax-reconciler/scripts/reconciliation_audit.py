#!/usr/bin/env python3
"""Compare resolved 1099-DA records against tax software disposals."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from common import find_value, load_records, parse_dt, stable_id, to_float, write_json


def _ts(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def normalize_tax_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for idx, row in enumerate(rows):
        asset = find_value(row, "asset")
        quantity = to_float(find_value(row, "quantity"))
        disposed_at = parse_dt(find_value(row, "disposed_at"))
        proceeds = to_float(find_value(row, "proceeds_usd"))
        basis = to_float(find_value(row, "cost_basis_usd"))
        gain = to_float(find_value(row, "gain_loss_usd"))
        normalized.append(
            {
                "record_id": stable_id([asset, quantity, disposed_at, proceeds, basis, "tax", idx]),
                "asset": asset,
                "quantity": quantity,
                "disposed_at": disposed_at,
                "proceeds_usd": proceeds,
                "cost_basis_usd": basis,
                "gain_loss_usd": gain,
                "raw": row,
            }
        )
    return normalized


def match_records(a: Dict[str, Any], b: Dict[str, Any], tolerance_seconds: int = 86400) -> bool:
    if (a.get("asset") or "").upper() != (b.get("asset") or "").upper():
        return False

    qa = a.get("quantity")
    qb = b.get("quantity")
    if qa is not None and qb is not None and abs(float(qa) - float(qb)) > 1e-8:
        return False

    ta = _ts(a.get("disposed_at"))
    tb = _ts(b.get("disposed_at"))
    if ta is not None and tb is not None and abs(ta - tb) > tolerance_seconds:
        return False

    return True


def audit(
    resolved_rows: List[Dict[str, Any]],
    tax_rows: List[Dict[str, Any]],
    tolerance_seconds: int = 86400,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    unmatched_tax: Set[int] = set(range(len(tax_rows)))
    exceptions: List[Dict[str, Any]] = []
    matched = 0
    unmatched_count = 0

    proceeds_delta_total = 0.0
    basis_delta_total = 0.0
    gain_delta_total = 0.0

    for resolved in resolved_rows:
        matched_pair = None
        for idx in list(unmatched_tax):
            tax = tax_rows[idx]
            if match_records(resolved, tax, tolerance_seconds=tolerance_seconds):
                matched_pair = (idx, tax)
                break

        if matched_pair is None:
            unmatched_count += 1
            exceptions.append(
                {
                    "id": resolved.get("record_id"),
                    "asset": resolved.get("asset"),
                    "date_time": resolved.get("disposed_at"),
                    "delta": None,
                    "likely_cause": "missing_or_unmapped_disposition",
                    "recommended_fix": "Verify import mapping and missing wallet/exchange data.",
                    "status": "open",
                }
            )
            continue

        idx, tax = matched_pair
        unmatched_tax.remove(idx)
        matched += 1

        proceeds_delta = (resolved.get("proceeds_usd") or 0.0) - (tax.get("proceeds_usd") or 0.0)
        basis_delta = (resolved.get("cost_basis_usd") or 0.0) - (tax.get("cost_basis_usd") or 0.0)
        gain_delta = (resolved.get("gain_loss_usd") or 0.0) - (tax.get("gain_loss_usd") or 0.0)

        proceeds_delta_total += proceeds_delta
        basis_delta_total += basis_delta
        gain_delta_total += gain_delta

        if any(abs(x) >= 0.01 for x in (proceeds_delta, basis_delta, gain_delta)):
            exceptions.append(
                {
                    "id": resolved.get("record_id"),
                    "asset": resolved.get("asset"),
                    "date_time": resolved.get("disposed_at"),
                    "delta": {
                        "proceeds_usd": round(proceeds_delta, 2),
                        "cost_basis_usd": round(basis_delta, 2),
                        "gain_loss_usd": round(gain_delta, 2),
                    },
                    "likely_cause": "basis_method_or_fee_treatment_difference",
                    "recommended_fix": "Align accounting method and fee treatment, then recompute.",
                    "status": "open",
                }
            )

    for idx in unmatched_tax:
        tax = tax_rows[idx]
        unmatched_count += 1
        exceptions.append(
            {
                "id": tax.get("record_id"),
                "asset": tax.get("asset"),
                "date_time": tax.get("disposed_at"),
                "delta": None,
                "likely_cause": "tax_software_row_without_corresponding_1099da_row",
                "recommended_fix": "Validate transfer classification and broker reporting scope.",
                "status": "open",
            }
        )

    summary = {
        "matched_count": matched,
        "unmatched_count": unmatched_count,
        "partial_matches": sum(1 for row in exceptions if isinstance(row.get("delta"), dict)),
        "total_proceeds_delta_usd": round(proceeds_delta_total, 2),
        "total_basis_delta_usd": round(basis_delta_total, 2),
        "total_gain_loss_delta_usd": round(gain_delta_total, 2),
    }
    return summary, exceptions


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit 1099-DA rows against tax software rows")
    parser.add_argument("--resolved", required=True, help="Resolved 1099-DA JSON path")
    parser.add_argument("--tax-input", required=True, help="Tax software CSV/JSON/JSONL path")
    parser.add_argument("--output", required=True, help="Output audit JSON path")
    parser.add_argument(
        "--time-tolerance-seconds",
        type=int,
        default=86400,
        help="Matching tolerance for disposal timestamps (default: 86400)",
    )
    args = parser.parse_args()

    payload = json.loads(Path(args.resolved).read_text(encoding="utf-8"))
    resolved_rows = payload.get("records", payload)
    if not isinstance(resolved_rows, list):
        raise ValueError("Resolved input must contain a list in 'records' or be a top-level array")

    tax_rows = normalize_tax_rows(load_records(args.tax_input))
    summary, exceptions = audit(
        resolved_rows=resolved_rows,
        tax_rows=tax_rows,
        tolerance_seconds=args.time_tolerance_seconds,
    )

    write_json(
        args.output,
        {
            "summary": summary,
            "exceptions": exceptions,
            "tax_rows": tax_rows,
        },
    )
    print(f"Audited {len(resolved_rows)} vs {len(tax_rows)} rows -> {args.output}")


if __name__ == "__main__":
    main()
