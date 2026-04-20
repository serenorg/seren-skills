"""Shared test fixtures."""

from __future__ import annotations

from typing import Any

import pytest


class FakeRunner:
    """Records calls and returns canned responses. Used in place of a live
    psycopg connection for offline critical tests."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple | None]] = []
        self._next_rows: list[list[dict[str, Any]]] = []
        self._next_error: Exception | None = None

    def queue_rows(self, rows: list[dict[str, Any]]) -> None:
        self._next_rows.append(rows)

    def queue_error(self, exc: Exception) -> None:
        self._next_error = exc

    def run(
        self, sql: str, params: tuple | None
    ) -> tuple[list[dict[str, Any]], int]:
        self.calls.append((sql, params))
        if self._next_error is not None:
            err, self._next_error = self._next_error, None
            raise err
        rows = self._next_rows.pop(0) if self._next_rows else []
        return rows, len(rows)


@pytest.fixture
def runner() -> FakeRunner:
    return FakeRunner()
