"""Visible, flushed per-lead progress for batch runs. Issue #850.

A batch enrich cycle can run 15-25 minutes (the ~90s ContentNote
throttle times up to `max_leads_per_daily_run` leads). The pre-#850
loops only printed on failure, and CPython block-buffers stdout when
it is redirected to a file — so an operator watching a `> log 2>&1`
batch saw an empty log for minutes, and a run killed mid-cycle left a
0-byte log with no record of which production Salesforce leads were
touched.

`BatchProgress` fixes both. On every event it:

  * prints a human-readable line to `stream` (default stderr) with
    `flush=True`, so a redirected log populates incrementally and the
    terminal shows live progress; and
  * appends one JSON object to `jsonl_path` (if given) and flushes it,
    so a batch interrupted at any point still leaves an accurate,
    machine-readable record of every lead touched — including the
    terminal status of any Note already written.

The write-then-flush-per-event design is deliberate: the audit line
must survive a SIGTERM that arrives during the next lead's enrichment
or during a throttle pause.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, TextIO

# Human-facing labels for terminal lead statuses. Keys match the
# `write_note` result statuses plus the dry-run `enriched` outcome and
# the catch-all `failed`.
_STATUS_LABELS = {
    "written": "NOTE WRITTEN",
    "enriched": "enriched (dry-run .docx)",
    "skipped_non_pk": "skipped (not Packaging)",
    "skipped_recent": "skipped (enriched within 24h)",
    "failed": "FAILED",
}


class BatchProgress:
    """Stream + JSONL progress sink for a single batch run."""

    def __init__(
        self,
        *,
        stream: Optional[TextIO] = None,
        jsonl_path: Optional[Path] = None,
        clock=None,
    ) -> None:
        self._stream = stream if stream is not None else sys.stderr
        self._jsonl_path = Path(jsonl_path) if jsonl_path is not None else None
        self._clock = clock or (lambda: datetime.now(tz=timezone.utc))
        if self._jsonl_path is not None:
            self._jsonl_path.parent.mkdir(parents=True, exist_ok=True)

    def lead_start(self, *, idx: int, total: int, record_id: str, name: str) -> None:
        self._emit(
            {
                "event": "lead_start",
                "idx": idx,
                "total": total,
                "record_id": record_id,
                "name": name,
            },
            human=f"[{idx}/{total}] {record_id} {name} — processing…",
        )

    def lead_done(
        self,
        *,
        idx: int,
        total: int,
        record_id: str,
        name: str,
        status: str,
        detail: Optional[str] = None,
    ) -> None:
        label = _STATUS_LABELS.get(status, status)
        human = f"[{idx}/{total}] {record_id} {name} — {label}"
        if detail:
            human += f": {detail}"
        self._emit(
            {
                "event": "lead_done",
                "idx": idx,
                "total": total,
                "record_id": record_id,
                "name": name,
                "status": status,
                "detail": detail,
            },
            human=human,
        )

    def pause(self, *, idx: int, total: int, seconds: int) -> None:
        self._emit(
            {"event": "pause", "idx": idx, "total": total, "seconds": seconds},
            human=(
                f"… pausing {seconds}s for the Salesforce ContentNote "
                f"throttle (after {idx}/{total})"
            ),
        )

    def _emit(self, payload: dict, *, human: str) -> None:
        record = {
            "ts": self._clock().strftime("%Y-%m-%dT%H:%M:%SZ"),
            **payload,
        }
        print(human, file=self._stream, flush=True)
        if self._jsonl_path is not None:
            with self._jsonl_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
                fh.flush()


class _NullProgress:
    """No-op sink. Default for callers that do not opt into progress
    (e.g. existing unit tests that drive the loops directly)."""

    def lead_start(self, **_kwargs) -> None:  # noqa: D401
        return None

    def lead_done(self, **_kwargs) -> None:
        return None

    def pause(self, **_kwargs) -> None:
        return None


NULL = _NullProgress()
