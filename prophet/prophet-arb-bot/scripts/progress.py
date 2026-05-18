"""Append-only JSONL progress stream for prophet-arb-bot.

Issue #640: `--command run --yes-live --json-output` buffers stdout until
the final envelope, so chat sees 20+ minutes of silence while the agent
drives 18 sequential Prophet `/create` entries. The fix is a side-channel
file at `<state_dir>/run_progress.jsonl` — one JSON line per stage event,
flushed per write so a crash mid-cycle preserves every line already on
disk.

This module owns ONLY the file format and the heartbeat thread. Call
sites in `agent.py` decide which stages to emit; failures here are
swallowed (`_safe_emit`) so telemetry can never crash the cycle.
"""

from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from state_paths import resolve_state_dir


CURRENT_FILE_NAME = "run_progress.jsonl"
PREV_FILE_NAME = "run_progress.prev.jsonl"


def _default_state_dir() -> Path:
    # Thin wrapper routed through the canonical resolver so progress.py
    # and discovery/candidate_sheet.py always agree on the state dir
    # (issue #693).
    return resolve_state_dir()


def _utc_now_iso() -> str:
    """Second-precision UTC timestamp ending in Z (matches issue #640 sample)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ProgressEmitter:
    """Writes one JSON line per stage event, never raises.

    Construction is cheap (no file I/O). The first `emit("cycle_start", …)`
    rotates the prior cycle's file to `run_progress.prev.jsonl` and
    truncates the current file, giving operators a one-tick history. All
    other stages append.
    """

    def __init__(self, state_dir: Path | None = None) -> None:
        self.state_dir = (state_dir or _default_state_dir()).expanduser()
        self.current_path = self.state_dir / CURRENT_FILE_NAME
        self.prev_path = self.state_dir / PREV_FILE_NAME
        self._rotated_this_cycle = False

    # ------------------------------------------------------------------
    # Public API

    def emit(self, stage: str, **fields: Any) -> None:
        """Append one event line. Never raises."""
        try:
            self._safe_emit(stage, fields)
        except Exception:
            # Telemetry must never crash the agent. Acceptance criterion
            # #5 of issue #640: the `--json-output` envelope is byte-
            # identical with or without progress streaming, so any I/O
            # error here is suppressed.
            return

    @contextmanager
    def heartbeat(
        self, *, idx: int, current: str, interval: float = 15.0
    ) -> Iterator[None]:
        """Emit `heartbeat` events every `interval` seconds inside the block.

        Used during long blocking ops (Prophet AI seed calc, hedge wait)
        so the chat-side Monitor knows the bot is alive. Heartbeats stop
        when the context exits.
        """
        stop = threading.Event()
        start = time.monotonic()

        def _loop() -> None:
            while not stop.wait(interval):
                elapsed = int(time.monotonic() - start)
                self.emit("heartbeat", idx=idx, current=current, elapsed_s=elapsed)

        thread = threading.Thread(target=_loop, daemon=True, name="progress-heartbeat")
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join(timeout=max(interval, 1.0) + 0.5)

    # ------------------------------------------------------------------
    # Internals

    def _safe_emit(self, stage: str, fields: dict[str, Any]) -> None:
        if stage == "cycle_start" and not self._rotated_this_cycle:
            self._rotate()
            self._rotated_this_cycle = True

        self.state_dir.mkdir(parents=True, exist_ok=True)
        line = {"ts": _utc_now_iso(), "stage": stage, **fields}
        # Open-append-close-flush per write so a kill -9 after this
        # function returns leaves every prior line on disk. fsync() is
        # intentionally not called — flushing to kernel buffers is enough
        # for the chat-side Monitor (which `tail -F`s the file) and keeps
        # the per-emit cost negligible.
        with self.current_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, separators=(",", ":")) + "\n")
            fh.flush()

    def _rotate(self) -> None:
        """Move current → prev, then truncate current.

        First-cycle case (no prior file): nothing to rotate, but we still
        need to truncate so a stale file from a previous skill version
        doesn't bleed into the new cycle's stream.
        """
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            return
        if self.current_path.exists():
            try:
                # os.replace is atomic on POSIX and Windows.
                os.replace(self.current_path, self.prev_path)
            except OSError:
                # Leave the file alone — better to append than to lose
                # the prior cycle's tail entirely.
                pass
