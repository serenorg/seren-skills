"""Schema guard — idempotent DDL for the family-office database.

Only permitted DDL path. Skill code never runs raw DDL — instead call
`run_schema_guard(conn)` at the start of every invocation. The guard is
idempotent: running twice against the same database is a no-op.

See family-office design doc, §5.
"""

from __future__ import annotations

from importlib import resources
from typing import Protocol

# Tables the guard must create. Used by tests and by diagnostics.
EXPECTED_TABLES: frozenset[str] = frozenset(
    {
        # Support
        "audit_log",
        "execution_log",
        "client_profile",
        # 16 canonical object families
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


class SchemaGuardError(Exception):
    """Raised when the schema guard cannot apply DDL."""


class _ConnLike(Protocol):
    def execute(self, query: str, params: tuple | None = None) -> object: ...
    def commit(self) -> None: ...


def load_schema_guard_sql() -> str:
    """Return the schema-guard SQL text from the packaged resource."""
    with resources.files(__package__).joinpath("sql/schema_guard.sql").open(
        "r", encoding="utf-8"
    ) as fh:
        return fh.read()


def run_schema_guard(conn: _ConnLike) -> None:
    """Apply the schema-guard DDL idempotently against a live connection.

    The caller supplies an open `psycopg` connection (or any object with
    compatible `execute`/`commit` semantics). The guard itself performs no
    authentication; that is the caller's responsibility.

    Raises:
        SchemaGuardError: If DDL application fails.
    """
    sql = load_schema_guard_sql()
    try:
        conn.execute(sql)
        conn.commit()
    except Exception as exc:  # pragma: no cover — surfaced in integration suite
        raise SchemaGuardError(f"schema guard failed: {exc!r}") from exc
