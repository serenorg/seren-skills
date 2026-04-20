"""Critical security invariant tests for audit_query.

These tests are non-negotiable. If one fails on main, ship is blocked.
"""

from __future__ import annotations

import logging
import re

import pytest

from family_office_base.audit_query import (
    TENANT_TABLES,
    AuditQueryError,
    audit_query,
)


def test_rejects_ddl(runner) -> None:
    with pytest.raises(AuditQueryError, match="DDL"):
        audit_query(
            "CREATE TABLE foo (id TEXT)",
            caller=__name__,
            runner=runner,
        )


def test_rejects_select_on_tenant_table_missing_confidentiality_filter(
    runner,
) -> None:
    # `obligation` is a tenant table. Without a confidentiality filter, blocked.
    with pytest.raises(AuditQueryError, match="confidentiality_label"):
        audit_query(
            "SELECT id FROM obligation WHERE due_date < now()",
            caller=__name__,
            runner=runner,
        )


def test_allows_select_on_tenant_table_with_confidentiality_filter(runner) -> None:
    runner.queue_rows([{"id": "obligation:1"}])
    rows = audit_query(
        "SELECT id FROM obligation "
        "WHERE due_date < now() AND confidentiality_label IN ('office-private')",
        caller=__name__,
        caller_role="office_operator",
        runner=runner,
    )
    assert rows == [{"id": "obligation:1"}]


def test_allows_select_on_support_table_without_confidentiality_filter(
    runner,
) -> None:
    # audit_log is a support table, not tenant. Filter is not required.
    runner.queue_rows([{"caller": "x"}])
    audit_query(
        "SELECT caller FROM audit_log ORDER BY started_at DESC",
        caller=__name__,
        runner=runner,
    )


def test_rejects_missing_caller(runner) -> None:
    with pytest.raises(AuditQueryError, match="caller"):
        audit_query(
            "SELECT 1",
            caller="",
            runner=runner,
        )


def test_rejects_multi_statement_sql(runner) -> None:
    with pytest.raises(AuditQueryError, match="multi-statement"):
        audit_query(
            "SELECT 1; SELECT 2",
            caller=__name__,
            runner=runner,
        )


def test_writes_audit_log_row_per_call(runner) -> None:
    runner.queue_rows([{"x": 1}])
    audit_query(
        "SELECT 1 AS x",
        caller=__name__,
        runner=runner,
    )
    # First call: the SELECT itself. Second call: the audit_log INSERT.
    assert len(runner.calls) == 2
    audit_sql, audit_params = runner.calls[1]
    assert "INSERT INTO audit_log" in audit_sql
    assert audit_params is not None
    caller, caller_role, sql_hash, _, _, _, _ = audit_params
    assert caller == __name__
    assert caller_role == "office_operator"
    assert len(sql_hash) == 64  # sha256 hex


def test_audit_log_never_contains_raw_sql_as_cleartext(
    runner, caplog: pytest.LogCaptureFixture
) -> None:
    # The raw SQL frequently contains PII. Only the hash may be logged.
    sensitive_sql = (
        "SELECT id FROM obligation "
        "WHERE description = 'SSN 123-45-6789' "
        "AND confidentiality_label = 'tax-sensitive'"
    )
    runner.queue_rows([])
    with caplog.at_level(logging.DEBUG, logger="family_office.audit_query"):
        audit_query(
            sensitive_sql,
            caller=__name__,
            caller_role="service_line_tax",
            runner=runner,
        )
    # No log record should contain the raw SSN or the WHERE clause literal.
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "123-45-6789" not in joined
    assert "WHERE description" not in joined
    # And the audit_log INSERT params carry the hash, not the raw SQL.
    _, audit_params = runner.calls[1]
    _, _, sql_hash, *_ = audit_params
    assert "SSN" not in sql_hash
    assert re.fullmatch(r"[0-9a-f]{64}", sql_hash)


def test_every_canonical_table_is_in_tenant_set() -> None:
    # Regression guard against forgetting a table in the enforcement set.
    for table in {
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
    }:
        assert table in TENANT_TABLES, (
            f"{table!r} missing from TENANT_TABLES — confidentiality not enforced"
        )
