#!/usr/bin/env python3
"""JSONL audit logging for Kraken Smart DCA Bot."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from datetime import UTC
except ImportError:  # pragma: no cover
    from datetime import timezone

    UTC = timezone.utc


class AuditLogger:
    """Writes auditable structured logs to JSONL files."""

    def __init__(self, logs_dir: str = "logs") -> None:
        self.logs_dir = Path(logs_dir)
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def _append(self, filename: str, payload: dict[str, Any]) -> None:
        payload = dict(payload)
        payload.setdefault("timestamp", datetime.now(tz=UTC).isoformat())
        with (self.logs_dir / filename).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")

    def log_event(self, event: str, payload: dict[str, Any]) -> None:
        self._append("events.jsonl", {"event": event, **payload})

    def log_execution(self, payload: dict[str, Any]) -> None:
        self._append("executions.jsonl", payload)

    def log_order(self, payload: dict[str, Any]) -> None:
        self._append("orders.jsonl", payload)

    def log_portfolio(self, payload: dict[str, Any]) -> None:
        self._append("portfolio.jsonl", payload)

    def log_scanner(self, payload: dict[str, Any]) -> None:
        self._append("scanner.jsonl", payload)

    def log_error(self, operation: str, message: str, context: dict[str, Any] | None = None) -> None:
        self._append(
            "errors.jsonl",
            {
                "operation": operation,
                "message": message,
                "context": context or {},
            },
        )
