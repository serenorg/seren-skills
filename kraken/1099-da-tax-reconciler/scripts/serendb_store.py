#!/usr/bin/env python3
"""Persist reconciliation artifacts into SerenDB via the Seren MCP server.

MCP-native implementation: uses mcp__seren-mcp__run_sql and
mcp__seren-mcp__run_sql_transaction instead of psycopg + SEREN_API_KEY.

When running inside Seren Desktop, the MCP server is already authenticated
through the user's login session -- no .env file or API key needed.

This module is designed to be called by the agent (Claude) using MCP tools.
The functions below produce the SQL statements and payloads that the agent
should execute via the MCP tools.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List


SCHEMA_DDL = """\
CREATE SCHEMA IF NOT EXISTS crypto_tax;

CREATE TABLE IF NOT EXISTS crypto_tax.reconciliation_runs (
  run_id TEXT PRIMARY KEY,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  input_1099da_path TEXT,
  input_tax_path TEXT,
  summary JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS crypto_tax.normalized_1099da (
  run_id TEXT NOT NULL,
  record_id TEXT NOT NULL,
  payload JSONB NOT NULL,
  PRIMARY KEY (run_id, record_id)
);

CREATE TABLE IF NOT EXISTS crypto_tax.resolved_lots (
  run_id TEXT NOT NULL,
  record_id TEXT NOT NULL,
  payload JSONB NOT NULL,
  PRIMARY KEY (run_id, record_id)
);

CREATE TABLE IF NOT EXISTS crypto_tax.reconciliation_exceptions (
  run_id TEXT NOT NULL,
  exception_id TEXT NOT NULL,
  payload JSONB NOT NULL,
  PRIMARY KEY (run_id, exception_id)
);
"""


def get_schema_ddl() -> str:
    """Return the DDL for the crypto_tax schema.

    The agent should execute this via:
        mcp__seren-mcp__run_sql(query=get_schema_ddl())
    """
    return SCHEMA_DDL


def build_upsert_run_sql(
    run_id: str,
    summary: Dict[str, Any],
    input_1099da_path: str,
    input_tax_path: str,
) -> str:
    """Return SQL to upsert a reconciliation run record.

    The agent should execute this via:
        mcp__seren-mcp__run_sql(query=build_upsert_run_sql(...))
    """
    summary_json = json.dumps(summary).replace("'", "''")
    safe_1099da = (input_1099da_path or "").replace("'", "''")
    safe_tax = (input_tax_path or "").replace("'", "''")
    safe_run = run_id.replace("'", "''")

    return f"""\
INSERT INTO crypto_tax.reconciliation_runs (run_id, input_1099da_path, input_tax_path, summary)
VALUES ('{safe_run}', '{safe_1099da}', '{safe_tax}', '{summary_json}'::jsonb)
ON CONFLICT (run_id) DO UPDATE SET
  input_1099da_path = EXCLUDED.input_1099da_path,
  input_tax_path = EXCLUDED.input_tax_path,
  summary = EXCLUDED.summary;
"""


def build_upsert_rows_sql(
    table: str,
    run_id: str,
    rows: Iterable[Dict[str, Any]],
    key_field: str,
) -> List[str]:
    """Return a list of SQL INSERT statements for batch execution.

    The agent should execute these via:
        mcp__seren-mcp__run_sql_transaction(queries=[...])
    """
    allowed_tables = {
        "normalized_1099da",
        "resolved_lots",
        "reconciliation_exceptions",
    }
    if table not in allowed_tables:
        raise ValueError(f"Unexpected table: {table}")
    if key_field not in {"record_id", "exception_id"}:
        raise ValueError(f"Unexpected key field: {key_field}")

    safe_run = run_id.replace("'", "''")
    statements: List[str] = []

    for idx, row in enumerate(rows):
        row_key = str(
            row.get(key_field)
            or row.get("record_id")
            or row.get("id")
            or f"row_{idx}"
        ).replace("'", "''")
        payload_json = json.dumps(row).replace("'", "''")

        statements.append(
            f"INSERT INTO crypto_tax.{table} (run_id, {key_field}, payload) "
            f"VALUES ('{safe_run}', '{row_key}', '{payload_json}'::jsonb) "
            f"ON CONFLICT (run_id, {key_field}) DO UPDATE SET payload = EXCLUDED.payload;"
        )

    return statements


def build_persist_transaction(
    run_id: str,
    normalized: List[Dict[str, Any]],
    resolved: List[Dict[str, Any]],
    exceptions: List[Dict[str, Any]],
    summary: Dict[str, Any],
    input_1099da_path: str,
    input_tax_path: str,
) -> List[str]:
    """Build all SQL statements needed to persist a full pipeline run.

    The agent should execute these via:
        mcp__seren-mcp__run_sql_transaction(queries=build_persist_transaction(...))

    Returns a list of SQL statements to run inside a single transaction.
    """
    statements: List[str] = []

    statements.append(get_schema_ddl())
    statements.append(build_upsert_run_sql(run_id, summary, input_1099da_path, input_tax_path))

    statements.extend(
        build_upsert_rows_sql("normalized_1099da", run_id, normalized, "record_id")
    )
    statements.extend(
        build_upsert_rows_sql("resolved_lots", run_id, resolved, "record_id")
    )

    shaped_exceptions = []
    for idx, item in enumerate(exceptions):
        exception_id = str(item.get("id") or item.get("exception_id") or f"exception_{idx}")
        shaped_exceptions.append({"exception_id": exception_id, **item})
    statements.extend(
        build_upsert_rows_sql("reconciliation_exceptions", run_id, shaped_exceptions, "exception_id")
    )

    return statements
