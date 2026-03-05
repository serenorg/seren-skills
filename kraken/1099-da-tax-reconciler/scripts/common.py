"""Shared helpers for crypto 1099-DA reconciliation scripts."""

from __future__ import annotations

import csv
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


COLUMN_SYNONYMS = {
    "asset": ["asset", "symbol", "currency", "token", "coin", "asset_symbol"],
    "quantity": ["quantity", "qty", "amount", "units", "disposed_quantity"],
    "disposed_at": [
        "disposed_at",
        "sale_date",
        "date_sold",
        "date_disposed",
        "transaction_date",
        "timestamp",
        "date",
    ],
    "proceeds_usd": ["proceeds_usd", "proceeds", "gross_proceeds", "sale_proceeds"],
    "cost_basis_usd": ["cost_basis_usd", "cost_basis", "basis", "adjusted_basis"],
    "gain_loss_usd": ["gain_loss_usd", "gain_loss", "gain", "loss", "pnl"],
    "fee_usd": ["fee_usd", "fee", "fees", "transaction_fee"],
    "holding_period": ["holding_period", "term", "short_long", "st_lt"],
    "acquired_at": ["acquired_at", "date_acquired", "purchase_date"],
    "broker": ["broker", "exchange", "platform", "source"],
    "tx_hash": ["tx_hash", "transaction_hash", "hash", "txid"],
}


def normalize_header(value: str) -> str:
    return value.strip().lower().replace(" ", "_")


def to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip().replace(",", "")
    if not text:
        return None
    if text.startswith("$"):
        text = text[1:]
    if text.startswith("(") and text.endswith(")"):
        text = f"-{text[1:-1]}"

    try:
        return float(text)
    except ValueError:
        return None


def parse_dt(value: Any) -> Optional[str]:
    if value is None:
        return None

    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if not text:
            return None
        text = text.replace("Z", "+00:00")
        dt = None

        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            pass

        if dt is None:
            for fmt in (
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d",
                "%m/%d/%Y",
                "%m/%d/%Y %H:%M:%S",
            ):
                try:
                    dt = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue

        if dt is None:
            return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def find_value(row: Dict[str, Any], key: str) -> Any:
    synonyms = COLUMN_SYNONYMS.get(key, [key])
    lowered = {normalize_header(k): v for k, v in row.items()}
    for name in synonyms:
        if name in lowered:
            return lowered[name]
    return None


def load_records(path: str) -> List[Dict[str, Any]]:
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")

    suffix = file_path.suffix.lower()
    if suffix in {".json", ".jsonl"}:
        raw = file_path.read_text(encoding="utf-8").strip()
        if not raw:
            return []
        if suffix == ".jsonl":
            return [json.loads(line) for line in raw.splitlines() if line.strip()]

        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [row for row in parsed if isinstance(row, dict)]
        if isinstance(parsed, dict):
            data = parsed.get("records")
            if isinstance(data, list):
                return [row for row in data if isinstance(row, dict)]
            return [parsed]
        return []

    with file_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return [dict(row) for row in reader]


def write_json(path: str, payload: Any) -> None:
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")


def env(name: str, default: Optional[str] = None, required: bool = False) -> Optional[str]:
    value = os.getenv(name, default)
    if required and not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def stable_id(parts: Iterable[Any]) -> str:
    text = "|".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
