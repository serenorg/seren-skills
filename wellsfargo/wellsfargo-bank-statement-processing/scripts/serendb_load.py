from __future__ import annotations

from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import psycopg


def _read_sql(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"SQL file not found: {path}")
    return path.read_text(encoding="utf-8")


def _month_start(date_iso: str | None) -> str | None:
    if not date_iso:
        return None
    dt = date.fromisoformat(date_iso)
    return dt.replace(day=1).isoformat()


def build_monthly_summary(transactions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    acc: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {
            "debit_total": 0.0,
            "credit_total": 0.0,
            "txn_count": 0,
        }
    )
    for txn in transactions:
        account_masked = str(txn.get("account_masked", "****"))
        month_start = _month_start(txn.get("txn_date"))
        if not month_start:
            continue
        amount = float(txn.get("amount", 0.0))
        key = (account_masked, month_start)
        if amount < 0:
            acc[key]["debit_total"] += amount
        else:
            acc[key]["credit_total"] += amount
        acc[key]["txn_count"] += 1

    rows: list[dict[str, Any]] = []
    for (account_masked, month_start), values in acc.items():
        rows.append(
            {
                "account_masked": account_masked,
                "month_start": month_start,
                "debit_total": round(values["debit_total"], 2),
                "credit_total": round(values["credit_total"], 2),
                "txn_count": values["txn_count"],
            }
        )
    return rows


def persist_run(
    database_url: str,
    schema_path: Path,
    views_path: Path,
    run_record: dict[str, Any],
    statement_files: list[dict[str, Any]],
    transactions: list[dict[str, Any]],
    categories: list[dict[str, Any]],
) -> None:
    monthly_summary = build_monthly_summary(transactions)

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(_read_sql(schema_path))
            cur.execute(_read_sql(views_path))

            cur.execute(
                """
                INSERT INTO wf_runs (
                  run_id, started_at, ended_at, status, mode,
                  error_code, selector_profile_version, artifact_root
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (run_id)
                DO UPDATE SET
                  ended_at = EXCLUDED.ended_at,
                  status = EXCLUDED.status,
                  error_code = EXCLUDED.error_code,
                  selector_profile_version = EXCLUDED.selector_profile_version,
                  artifact_root = EXCLUDED.artifact_root
                """,
                (
                    run_record["run_id"],
                    run_record["started_at"],
                    run_record["ended_at"],
                    run_record["status"],
                    run_record["mode"],
                    run_record.get("error_code"),
                    run_record.get("selector_profile_version"),
                    run_record["artifact_root"],
                ),
            )

            cur.executemany(
                """
                INSERT INTO wf_statement_files (
                  file_id, run_id, account_masked,
                  statement_period_start, statement_period_end,
                  local_file_path, sha256, bytes, download_status
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (file_id)
                DO UPDATE SET
                  run_id = EXCLUDED.run_id,
                  account_masked = EXCLUDED.account_masked,
                  statement_period_start = EXCLUDED.statement_period_start,
                  statement_period_end = EXCLUDED.statement_period_end,
                  local_file_path = EXCLUDED.local_file_path,
                  sha256 = EXCLUDED.sha256,
                  bytes = EXCLUDED.bytes,
                  download_status = EXCLUDED.download_status
                """,
                [
                    (
                        row["file_id"],
                        run_record["run_id"],
                        row["account_masked"],
                        row.get("statement_period_start"),
                        row.get("statement_period_end"),
                        row["local_file_path"],
                        row["sha256"],
                        int(row["bytes"]),
                        row["download_status"],
                    )
                    for row in statement_files
                ],
            )

            cur.executemany(
                """
                INSERT INTO wf_transactions (
                  row_hash, run_id, file_id, account_masked,
                  txn_date, post_date, description_raw, amount,
                  currency, statement_period_start, statement_period_end
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (row_hash)
                DO UPDATE SET
                  run_id = EXCLUDED.run_id,
                  file_id = EXCLUDED.file_id,
                  account_masked = EXCLUDED.account_masked,
                  txn_date = EXCLUDED.txn_date,
                  post_date = EXCLUDED.post_date,
                  description_raw = EXCLUDED.description_raw,
                  amount = EXCLUDED.amount,
                  currency = EXCLUDED.currency,
                  statement_period_start = EXCLUDED.statement_period_start,
                  statement_period_end = EXCLUDED.statement_period_end
                """,
                [
                    (
                        row["row_hash"],
                        run_record["run_id"],
                        row.get("file_id"),
                        row["account_masked"],
                        row.get("txn_date"),
                        row.get("post_date"),
                        row["description_raw"],
                        float(row["amount"]),
                        row.get("currency", "USD"),
                        row.get("statement_period_start"),
                        row.get("statement_period_end"),
                    )
                    for row in transactions
                ],
            )

            cur.executemany(
                """
                INSERT INTO wf_txn_categories (
                  row_hash, category_source, category,
                  confidence, rationale_short
                ) VALUES (%s,%s,%s,%s,%s)
                ON CONFLICT (row_hash)
                DO UPDATE SET
                  category_source = EXCLUDED.category_source,
                  category = EXCLUDED.category,
                  confidence = EXCLUDED.confidence,
                  rationale_short = EXCLUDED.rationale_short,
                  updated_at = NOW()
                """,
                [
                    (
                        row["row_hash"],
                        row["category_source"],
                        row["category"],
                        float(row.get("confidence", 0.0)),
                        row.get("rationale_short"),
                    )
                    for row in categories
                ],
            )

            cur.execute("DELETE FROM wf_monthly_summary WHERE run_id = %s", (run_record["run_id"],))
            cur.executemany(
                """
                INSERT INTO wf_monthly_summary (
                  run_id, account_masked, month_start,
                  debit_total, credit_total, txn_count
                ) VALUES (%s,%s,%s,%s,%s,%s)
                """,
                [
                    (
                        run_record["run_id"],
                        row["account_masked"],
                        row["month_start"],
                        float(row["debit_total"]),
                        float(row["credit_total"]),
                        int(row["txn_count"]),
                    )
                    for row in monthly_summary
                ],
            )

        conn.commit()
