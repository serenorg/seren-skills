"""Critical tests for the schema guard.

Offline: verifies the SQL file declares every table the canonical model
requires and carries the key safety invariants (office singleton CHECK,
obligation owner/source_basis CHECK, approval.granted_at immutability trigger,
event append-only triggers).

Online (skipped unless FAMILY_OFFICE_TEST_PG is set): applies the DDL twice
against a live PostgreSQL to prove idempotency and that information_schema
agrees with EXPECTED_TABLES.
"""

from __future__ import annotations

import os
import re

import pytest

from family_office_base.schema_guard import (
    EXPECTED_TABLES,
    load_schema_guard_sql,
    run_schema_guard,
)


@pytest.fixture(scope="module")
def guard_sql() -> str:
    return load_schema_guard_sql()


def test_ddl_declares_every_expected_table(guard_sql: str) -> None:
    for table in EXPECTED_TABLES:
        pattern = rf"CREATE TABLE IF NOT EXISTS\s+{re.escape(table)}\b"
        assert re.search(pattern, guard_sql, re.IGNORECASE), (
            f"schema guard SQL missing CREATE TABLE IF NOT EXISTS for {table!r}"
        )


def test_office_is_singleton_with_id_check(guard_sql: str) -> None:
    # The office table must constrain id to the literal 'office:singleton'.
    assert "CHECK (id = 'office:singleton')" in guard_sql


def test_obligation_has_owner_and_source_basis_checks(guard_sql: str) -> None:
    assert "obligation_has_owner" in guard_sql
    assert "obligation_has_source_basis" in guard_sql


def test_approval_granted_at_is_immutable_by_trigger(guard_sql: str) -> None:
    assert "approval_granted_at_immutable" in guard_sql
    assert "approval.granted_at is immutable" in guard_sql


def test_event_is_append_only_by_trigger(guard_sql: str) -> None:
    assert "event_append_only" in guard_sql
    assert "event_no_update" in guard_sql
    assert "event_no_delete" in guard_sql


def test_review_state_is_constrained_enum(guard_sql: str) -> None:
    # Prevents a typo like `review_state = 'aproved'` from ever persisting.
    assert "review_state IN" in guard_sql
    for state in (
        "pending",
        "approved",
        "rejected",
        "returned",
        "expired",
        "executed",
    ):
        assert f"'{state}'" in guard_sql


@pytest.mark.skipif(
    not os.environ.get("FAMILY_OFFICE_TEST_PG"),
    reason="set FAMILY_OFFICE_TEST_PG=postgres://... to run online guard test",
)
def test_schema_guard_is_idempotent_against_live_pg() -> None:
    import psycopg  # type: ignore[import-not-found]

    dsn = os.environ["FAMILY_OFFICE_TEST_PG"]
    with psycopg.connect(dsn, autocommit=False) as conn:
        run_schema_guard(conn)
        run_schema_guard(conn)  # second apply must succeed — idempotent
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public'"
            )
            present = {row[0] for row in cur.fetchall()}
    missing = EXPECTED_TABLES - present
    assert not missing, f"tables missing after guard: {missing}"
