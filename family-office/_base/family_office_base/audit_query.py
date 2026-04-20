"""audit_query — the only permitted read/write path to the family-office DB.

Single-family-office installation: no row-level tenancy. But every SELECT on
a tenant table MUST include a `confidentiality_label IN (...)` filter derived
from the caller's role, and every call is logged to `audit_log` by
`(caller, sql_hash, param_count, row_count, duration_ms, error_class)`.

DDL is rejected — use `run_schema_guard` for that.

Design intent: make the safe path easy and the unsafe path impossible without
active bypass. Any leaf-skill code that reaches for `psycopg.connect` directly
is a P0 defect.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import Any, Protocol

import sqlparse

from .confidentiality import visible_labels_for_role

logger = logging.getLogger("family_office.audit_query")

# Tables that must enforce the confidentiality filter on SELECT.
# Every canonical object family counts. Support tables (audit_log,
# execution_log, client_profile) do not carry object-level labels.
TENANT_TABLES: frozenset[str] = frozenset(
    {
        "office",
        "person",
        "advisor",
        "entity",
        "account",
        "asset",
        "document",
        "artifact",
        "decision",
        "policy",
        "task",
        "obligation",
        "approval",
        "communication",
        "event",
        "review_item",
    }
)

_DDL_KEYWORDS: frozenset[str] = frozenset(
    {"CREATE", "ALTER", "DROP", "TRUNCATE", "GRANT", "REVOKE"}
)


class AuditQueryError(Exception):
    """Raised when audit_query rejects a query before execution."""


class Runner(Protocol):
    """Query transport. The production runner wraps a `psycopg` connection;
    tests inject a fake that records calls and returns canned responses."""

    def run(
        self, sql: str, params: tuple | None
    ) -> tuple[list[dict[str, Any]], int]:
        """Execute the query and return (rows, rowcount)."""


def _classify_statement(parsed: sqlparse.sql.Statement) -> str:
    """Return the top-level statement keyword in upper-case (SELECT/INSERT/…)."""
    for token in parsed.tokens:
        if token.ttype in (sqlparse.tokens.DML, sqlparse.tokens.DDL):
            return token.value.upper()
    return ""


def _first_table_after_from_or_into(parsed: sqlparse.sql.Statement) -> str | None:
    """Best-effort table-name extraction for the first FROM/INTO/UPDATE target."""
    tokens = [t for t in parsed.flatten() if not t.is_whitespace]
    for i, tok in enumerate(tokens):
        if tok.ttype is sqlparse.tokens.Keyword and tok.value.upper() in (
            "FROM",
            "INTO",
            "UPDATE",
        ):
            for j in range(i + 1, len(tokens)):
                nxt = tokens[j]
                if nxt.ttype is sqlparse.tokens.Name:
                    return nxt.value.lower().strip('"')
    return None


def _sql_has_confidentiality_filter(sql: str, visible: frozenset[str]) -> bool:
    """Cheap textual check: the SQL must mention confidentiality_label and an IN
    list (or equality) constrained to the caller's visible labels.

    We deliberately use a textual check rather than a full AST walk: anything
    more permissive is a foot-gun; anything more strict blocks perfectly safe
    queries. Authors MUST explicitly cite `confidentiality_label IN (...)` or
    `confidentiality_label = '...'` in every tenant-table SELECT.
    """
    lowered = sql.lower()
    if "confidentiality_label" not in lowered:
        return False
    # accept either `confidentiality_label IN (...)` or `= '<label>'`.
    # We don't attempt to validate the literal list here; a mismatched list is
    # an authoring bug caught in code review, not by the enforcer. The enforcer
    # exists to prevent the common mistake: forgetting the filter entirely.
    pattern = r"confidentiality_label\s*(=|in)\s*"
    return re.search(pattern, lowered) is not None


def audit_query(
    sql: str,
    params: tuple | None = None,
    *,
    caller: str,
    caller_role: str = "office_operator",
    runner: Runner,
) -> list[dict[str, Any]]:
    """Run a query with audit logging + confidentiality enforcement.

    Args:
        sql: The SQL statement. DDL is rejected.
        params: Parameters for parameterized query. Never inlined into `sql`.
        caller: A stable identifier for the call site (typically `__name__`).
        caller_role: Role controlling confidentiality visibility.
        runner: Transport. Production runner wraps a psycopg connection.

    Returns:
        List of row dicts.

    Raises:
        AuditQueryError: On DDL, missing confidentiality filter, or empty caller.
    """
    if not caller:
        raise AuditQueryError("caller is required")

    parsed_statements = sqlparse.parse(sql)
    if not parsed_statements:
        raise AuditQueryError("empty SQL")
    if len(parsed_statements) > 1:
        # multiple statements in one call hides risk; require one at a time
        raise AuditQueryError("multi-statement SQL is not permitted")

    parsed = parsed_statements[0]
    statement_kw = _classify_statement(parsed)

    if statement_kw in _DDL_KEYWORDS:
        raise AuditQueryError(
            "DDL rejected by audit_query; use run_schema_guard instead"
        )

    if statement_kw == "SELECT":
        table = _first_table_after_from_or_into(parsed)
        if table in TENANT_TABLES:
            visible = visible_labels_for_role(caller_role)
            if not _sql_has_confidentiality_filter(sql, visible):
                raise AuditQueryError(
                    f"SELECT against tenant table {table!r} missing "
                    "confidentiality_label filter"
                )

    sql_hash = hashlib.sha256(sql.encode("utf-8")).hexdigest()
    param_count = len(params) if params else 0
    started = time.monotonic()
    rows: list[dict[str, Any]] = []
    rowcount = 0
    error_class: str | None = None
    try:
        rows, rowcount = runner.run(sql, params)
    except Exception as exc:
        error_class = exc.__class__.__name__
        raise
    finally:
        duration_ms = int((time.monotonic() - started) * 1000)
        # Log only the hash, never the raw SQL — SQL often contains PII.
        logger.debug(
            "audit_query call caller=%s role=%s sql_hash=%s params=%d "
            "rows=%d duration_ms=%d err=%s",
            caller,
            caller_role,
            sql_hash[:16],
            param_count,
            rowcount,
            duration_ms,
            error_class or "",
        )
        # Best-effort audit_log write via the same runner. Swallow failures
        # from the audit write so a transient audit issue does not mask the
        # real query outcome; but surface them at WARNING.
        try:
            runner.run(
                "INSERT INTO audit_log "
                "(caller, caller_role, sql_hash, param_count, row_count, "
                "duration_ms, error_class) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    caller,
                    caller_role,
                    sql_hash,
                    param_count,
                    rowcount,
                    duration_ms,
                    error_class,
                ),
            )
        except Exception as exc:  # pragma: no cover — diagnostics path
            logger.warning(
                "audit_log insert failed caller=%s class=%s",
                caller,
                exc.__class__.__name__,
            )

    return rows
