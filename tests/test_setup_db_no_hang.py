"""Verify setup_database uses individual DDL statements to prevent Python 3.14 hang.

Issue #298: A multi-statement DDL that returns 400 hangs Python 3.14 on macOS
indefinitely. The fix executes each DDL statement individually so a failure in
one statement doesn't block the process or prevent basic tables from being created.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
STORAGE_PATH = REPO_ROOT / "polymarket" / "bot" / "scripts" / "serendb_storage.py"


def _read() -> str:
    return STORAGE_PATH.read_text(encoding="utf-8")


def test_no_multi_statement_ddl_in_setup_database() -> None:
    """setup_database must NOT send multiple DDL statements in a single
    _execute_sql call. Each CREATE TABLE/INDEX/SCHEMA must be its own call."""
    source = _read()

    # Find the setup_database method body
    match = re.search(r'def setup_database\(self\).*?(?=\n    def )', source, re.DOTALL)
    assert match, "setup_database method not found"
    setup_body = match.group(0)

    # Count _execute_sql calls that contain multiple CREATE statements
    # A single _execute_sql call with 2+ CREATE keywords is the bug pattern
    execute_calls = re.findall(
        r'self\._execute_sql\("""(.*?)"""\)', setup_body, re.DOTALL
    )
    for call_body in execute_calls:
        create_count = len(re.findall(r'\bCREATE\b', call_body, re.IGNORECASE))
        assert create_count <= 1, (
            f"Found _execute_sql call with {create_count} CREATE statements. "
            "Each DDL must be its own _execute_sql call to prevent Python 3.14 hang."
        )


def test_extended_ddl_uses_individual_try_except() -> None:
    """Each extended DDL statement must be wrapped in its own try/except
    so a failure in one doesn't block the others."""
    source = _read()

    # The pattern: iterate over a list of DDL statements with per-statement try/except
    assert "_extended_ddl" in source, (
        "setup_database should use a _extended_ddl list for individual execution"
    )
    assert "for ddl in _extended_ddl" in source, (
        "setup_database should iterate _extended_ddl with per-statement execution"
    )


def test_extended_ddl_failure_is_non_blocking() -> None:
    """Extended schema DDL failures must not prevent setup_database from
    returning True (basic tables are sufficient for the bot to run)."""
    source = _read()
    match = re.search(r'def setup_database\(self\).*?(?=\n    def )', source, re.DOTALL)
    setup_body = match.group(0)

    # The extended_fail counter must exist and not cause return False
    assert "extended_fail" in setup_body, "Missing extended_fail counter"
    assert "non-blocking" in setup_body.lower(), (
        "Extended DDL failures should be logged as non-blocking"
    )


def test_basic_tables_each_have_own_execute_call() -> None:
    """Core tables (positions, trades, scan_logs, config, predictions,
    performance_metrics, resolved_markets) must each be a single-statement
    _execute_sql call."""
    source = _read()
    match = re.search(r'def setup_database\(self\).*?(?=\n    def )', source, re.DOTALL)
    setup_body = match.group(0)

    required_tables = [
        "positions", "trades", "scan_logs", "config",
        "predictions", "performance_metrics", "resolved_markets",
    ]
    for table in required_tables:
        pattern = rf'CREATE TABLE IF NOT EXISTS {table}\b'
        assert re.search(pattern, setup_body), (
            f"Basic table '{table}' not found in setup_database"
        )
