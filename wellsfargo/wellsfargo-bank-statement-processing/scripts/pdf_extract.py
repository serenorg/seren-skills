from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pdfplumber
from common import mask_account, parse_date_to_iso, sha256_text


@dataclass
class ParseResult:
    metadata: dict[str, Any]
    transactions: list[dict[str, Any]]
    parse_errors: list[str]


def _extract_metadata(raw_text: str) -> dict[str, Any]:
    account_raw = None
    account_match = re.search(
        r"(?:account|acct)(?:\s+number)?[:\s]*([Xx*\d\- ]{4,30})",
        raw_text,
        re.IGNORECASE,
    )
    if account_match:
        account_raw = account_match.group(1)

    period_start = None
    period_end = None
    period_match = re.search(
        r"(\d{1,2}/\d{1,2}/\d{2,4})\s*[-–]\s*(\d{1,2}/\d{1,2}/\d{2,4})",
        raw_text,
    )
    if period_match:
        period_start = parse_date_to_iso(period_match.group(1))
        period_end = parse_date_to_iso(period_match.group(2))

    return {
        "account_masked": mask_account(account_raw),
        "statement_period_start": period_start,
        "statement_period_end": period_end,
        "currency": "USD",
    }


def _amount_to_decimal(raw_amount: str) -> float:
    text = raw_amount.strip().replace("$", "").replace(",", "")
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]
    value = float(text)
    return -abs(value) if negative else value


def _normalize_mmdd(mmdd: str, year_hint: int) -> str | None:
    mmdd = mmdd.strip()
    if not re.match(r"^\d{1,2}/\d{1,2}$", mmdd):
        return parse_date_to_iso(mmdd)
    month, day = mmdd.split("/")
    try:
        return date(year_hint, int(month), int(day)).isoformat()
    except ValueError:
        return None


def _parse_transaction_line(line: str, year_hint: int) -> dict[str, Any] | None:
    pattern = re.compile(
        r"^(?P<txn>\d{1,2}/\d{1,2})(?:\s+(?P<post>\d{1,2}/\d{1,2}))?\s+(?P<desc>.+?)\s+(?P<amount>\(?-?\$?\d[\d,]*\.\d{2}\)?)$"
    )
    match = pattern.match(line.strip())
    if not match:
        return None

    txn_date = _normalize_mmdd(match.group("txn"), year_hint)
    post_date = _normalize_mmdd(match.group("post"), year_hint) if match.group("post") else None
    description = re.sub(r"\s+", " ", match.group("desc")).strip()
    amount = _amount_to_decimal(match.group("amount"))
    return {
        "txn_date": txn_date,
        "post_date": post_date,
        "description_raw": description,
        "amount": round(amount, 2),
    }


def parse_statement_pdf(pdf_path: Path, file_id: str) -> ParseResult:
    parse_errors: list[str] = []
    all_lines: list[str] = []
    first_page_text = ""

    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            for idx, page in enumerate(pdf.pages):
                text = page.extract_text() or ""
                if idx == 0:
                    first_page_text = text
                for line in text.splitlines():
                    line = line.strip()
                    if line:
                        all_lines.append(line)
    except Exception as exc:
        parse_errors.append(f"pdf_open_error: {exc}")
        return ParseResult(metadata={}, transactions=[], parse_errors=parse_errors)

    metadata = _extract_metadata(first_page_text)
    period_end = metadata.get("statement_period_end")
    year_hint = 2000
    if period_end and re.match(r"^\d{4}-\d{2}-\d{2}$", period_end):
        year_hint = int(period_end[:4])

    transactions: list[dict[str, Any]] = []
    for line in all_lines:
        parsed = _parse_transaction_line(line, year_hint=year_hint)
        if not parsed:
            continue
        row_hash = sha256_text(
            f"{file_id}|{parsed['txn_date']}|{parsed['description_raw']}|{parsed['amount']}"
        )
        parsed["row_hash"] = row_hash
        parsed["file_id"] = file_id
        parsed["account_masked"] = metadata.get("account_masked", "****")
        parsed["currency"] = metadata.get("currency", "USD")
        parsed["statement_period_start"] = metadata.get("statement_period_start")
        parsed["statement_period_end"] = metadata.get("statement_period_end")
        transactions.append(parsed)

    if not transactions:
        parse_errors.append("no_transactions_detected")

    return ParseResult(metadata=metadata, transactions=transactions, parse_errors=parse_errors)
