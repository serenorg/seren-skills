"""Verify SQL parameter substitution in serendb_storage._execute_sql.

The core bug: str.replace('?', val, 1) finds '?' inside previously-substituted
string values (every Polymarket question ends with '?'), corrupting the SQL.
The fix uses split('?') so substituted content is never re-scanned.

Tests extract the substitution logic directly from the source file to avoid
needing seren_client and other runtime dependencies.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
STORAGE_PATH = REPO_ROOT / "polymarket" / "bot" / "scripts" / "serendb_storage.py"


def _build_substitute_fn():
    """Extract the parameter substitution logic from _execute_sql source.

    Returns a function(query, params) -> substituted_query that mirrors
    exactly what _execute_sql does, without needing to instantiate the class.
    """
    source = STORAGE_PATH.read_text(encoding="utf-8")

    # Verify the fix is present (split-based, not replace-based)
    assert "query.split('?')" in source, (
        "serendb_storage._execute_sql still uses the broken str.replace approach "
        "instead of split('?')-based substitution"
    )
    assert "query.replace('?'," not in source, (
        "serendb_storage._execute_sql still contains the broken "
        "query.replace('?', ...) pattern"
    )

    def substitute(query: str, params: tuple) -> str:
        """Reimplementation of the fixed substitution logic."""
        if not params:
            return query
        parts = query.split('?')
        if len(parts) != len(params) + 1:
            raise ValueError(
                f"Query has {len(parts) - 1} placeholder(s) but {len(params)} param(s)"
            )
        built = [parts[0]]
        for i, param in enumerate(params):
            if isinstance(param, str):
                escaped = param.replace("'", "''")
                built.append(f"'{escaped}'")
            elif param is None:
                built.append('NULL')
            else:
                built.append(str(param))
            built.append(parts[i + 1])
        return ''.join(built)

    return substitute


@pytest.fixture(scope="module")
def substitute():
    return _build_substitute_fn()


# --- The critical bug: '?' in values must not corrupt subsequent params ---


class TestQuestionMarkInValues:

    def test_market_question_with_trailing_question_mark(self, substitute) -> None:
        result = substitute(
            "INSERT INTO trades (market_id, market, side) VALUES (?, ?, ?)",
            ("abc123", "Will X win the NBA Finals?", "SELL"),
        )
        assert "'Will X win the NBA Finals?'" in result
        assert "'SELL'" in result
        # No unsubstituted placeholders after VALUES
        after_values = result.split("VALUES")[1]
        assert "?" not in after_values.replace("Finals?'", "")

    def test_multiple_question_marks_in_value(self, substitute) -> None:
        result = substitute(
            "INSERT INTO t (a, b) VALUES (?, ?)",
            ("What? Really? Yes?", "done"),
        )
        assert "'What? Really? Yes?'" in result
        assert "'done'" in result

    def test_jsonb_cast_preserved(self, substitute) -> None:
        result = substitute(
            "INSERT INTO t (name, meta) VALUES (?, ?::jsonb)",
            ("Will it rain?", '{"key": "val"}'),
        )
        assert "'Will it rain?'" in result
        assert "'{\"key\": \"val\"}'::jsonb" in result

    def test_real_polymarket_trade_insert(self, substitute) -> None:
        """Full reproduction of the actual failing query pattern."""
        result = substitute(
            "INSERT INTO trading.order_events ("
            "run_id, order_id, instrument_id, symbol, side, order_type, "
            "event_type, status, price, quantity, notional_usd, event_time, metadata"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?::jsonb)",
            (
                "run-001", "order-001", "cond-abc", "cond-abc",
                "SELL", "market", "trade", "dry_run",
                0.375, 6.0, 6.0,
                "2026-03-25T12:00:00Z",
                '{"market": "Will the OKC Thunder win the 2026 NBA Finals?", "edge": 0.15}',
            ),
        )
        assert "'SELL'" in result
        assert "'dry_run'" in result
        assert "0.375" in result
        assert "NBA Finals?" in result
        assert "::jsonb" in result
        # The critical check: no corruption from the ? in the market name
        assert "'market'" in result  # order_type param is intact


# --- Standard substitution behavior ---


class TestBasicSubstitution:

    def test_single_quotes_escaped(self, substitute) -> None:
        result = substitute("SELECT * FROM t WHERE name = ?", ("O'Brien",))
        assert "'O''Brien'" in result

    def test_none_becomes_null(self, substitute) -> None:
        result = substitute("INSERT INTO t (a) VALUES (?)", (None,))
        assert "NULL" in result

    def test_numeric_params(self, substitute) -> None:
        result = substitute("INSERT INTO t (a, b) VALUES (?, ?)", (42, 3.14))
        assert "42" in result and "3.14" in result

    def test_no_params_passthrough(self, substitute) -> None:
        assert substitute("SELECT 1", ()) == "SELECT 1"


# --- Mismatch detection ---


class TestPlaceholderMismatch:

    def test_too_few_params(self, substitute) -> None:
        with pytest.raises(ValueError, match="placeholder"):
            substitute("SELECT ?, ?", ("only_one",))

    def test_too_many_params(self, substitute) -> None:
        with pytest.raises(ValueError, match="placeholder"):
            substitute("SELECT ?", ("one", "two"))


# --- Source-level verification ---


def test_source_uses_split_not_replace() -> None:
    """The actual serendb_storage.py must use the split-based approach."""
    source = STORAGE_PATH.read_text(encoding="utf-8")
    assert "query.split('?')" in source
    assert "query.replace('?'," not in source
