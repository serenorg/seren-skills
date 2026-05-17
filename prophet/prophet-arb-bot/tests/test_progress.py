"""Tests for the run-progress JSONL emitter (issue #640).

The emitter writes one JSON line per stage event to
`<state_dir>/run_progress.jsonl` and rotates the prior cycle's file to
`run_progress.prev.jsonl` on `cycle_start`. Open-append-close-flush per
write so a crash mid-cycle preserves every line already on disk.

Critical-path coverage only:
1. emit() appends a well-formed JSON line.
2. cycle_start truncates and rotates prev (one-tick history).
3. Crash mid-cycle leaves the last emitted line on disk.
4. heartbeat context manager emits at the configured cadence.
5. PROPHET_ARB_STATE_DIR env override redirects the path.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from progress import ProgressEmitter


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_emit_appends_jsonl_line(tmp_path: Path) -> None:
    emitter = ProgressEmitter(state_dir=tmp_path)
    emitter.emit("cycle_start", tick_id="t_1", mode="run", yes_live=True)
    emitter.emit("auth_ok", path="mcp")

    lines = _read_jsonl(tmp_path / "run_progress.jsonl")
    assert len(lines) == 2
    assert lines[0]["stage"] == "cycle_start"
    assert lines[0]["tick_id"] == "t_1"
    assert lines[0]["mode"] == "run"
    assert lines[0]["yes_live"] is True
    assert "ts" in lines[0] and lines[0]["ts"].endswith("Z")
    assert lines[1] == {**lines[1], "stage": "auth_ok", "path": "mcp"}


def test_cycle_start_rotates_prev_and_truncates(tmp_path: Path) -> None:
    # First cycle writes a few events.
    e1 = ProgressEmitter(state_dir=tmp_path)
    e1.emit("cycle_start", tick_id="t_1")
    e1.emit("entry_start", idx=1, total=2)
    e1.emit("cycle_end", status="ok")

    current = tmp_path / "run_progress.jsonl"
    prev = tmp_path / "run_progress.prev.jsonl"
    assert _read_jsonl(current)[-1]["stage"] == "cycle_end"
    assert not prev.exists(), "first cycle has no prior to rotate"

    # Second cycle: starting a new cycle rotates the prior to .prev.jsonl
    # and truncates the current file.
    e2 = ProgressEmitter(state_dir=tmp_path)
    e2.emit("cycle_start", tick_id="t_2")

    assert prev.exists()
    prev_lines = _read_jsonl(prev)
    assert prev_lines[0]["tick_id"] == "t_1"
    assert prev_lines[-1]["stage"] == "cycle_end"

    current_lines = _read_jsonl(current)
    assert len(current_lines) == 1
    assert current_lines[0]["tick_id"] == "t_2"


def test_crash_mid_cycle_preserves_lines(tmp_path: Path) -> None:
    """If the process dies after an emit() returns, the line is on disk.

    Acceptance criterion (#640 §7): open-append-close-flush per write.
    """
    emitter = ProgressEmitter(state_dir=tmp_path)
    emitter.emit("cycle_start", tick_id="t_1")
    emitter.emit("entry_start", idx=1, total=18, question="Yankees")
    # Simulate the agent crashing — drop the emitter reference without a
    # graceful close. The on-disk JSONL must still contain both lines.
    del emitter

    lines = _read_jsonl(tmp_path / "run_progress.jsonl")
    assert [ln["stage"] for ln in lines] == ["cycle_start", "entry_start"]
    assert lines[-1]["question"] == "Yankees"


def test_heartbeat_emits_at_cadence(tmp_path: Path) -> None:
    emitter = ProgressEmitter(state_dir=tmp_path)
    emitter.emit("cycle_start", tick_id="t_1")

    with emitter.heartbeat(idx=1, current="ai_calc", interval=0.05):
        time.sleep(0.18)

    lines = _read_jsonl(tmp_path / "run_progress.jsonl")
    heartbeats = [ln for ln in lines if ln["stage"] == "heartbeat"]
    # In 0.18s at a 0.05s cadence we expect 2 or 3 ticks. Lower bound
    # avoids flakiness on slow CI; upper bound is just sanity.
    assert 2 <= len(heartbeats) <= 4
    assert all(ln["idx"] == 1 and ln["current"] == "ai_calc" for ln in heartbeats)
    assert all("elapsed_s" in ln for ln in heartbeats)
    # Heartbeats must STOP after the context manager exits.
    snapshot = len(heartbeats)
    time.sleep(0.12)
    fresh_heartbeats = [
        ln for ln in _read_jsonl(tmp_path / "run_progress.jsonl") if ln["stage"] == "heartbeat"
    ]
    assert len(fresh_heartbeats) == snapshot, "heartbeat must stop on context exit"


def test_state_dir_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROPHET_ARB_STATE_DIR", str(tmp_path))
    emitter = ProgressEmitter()  # no explicit state_dir
    emitter.emit("cycle_start", tick_id="t_env")

    assert (tmp_path / "run_progress.jsonl").exists()
    lines = _read_jsonl(tmp_path / "run_progress.jsonl")
    assert lines[0]["tick_id"] == "t_env"


def test_emit_is_resilient_to_unwritable_dir(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """Telemetry must never crash the agent. A bad state_dir is a no-op.

    The arb-bot's --json-output envelope contract is byte-identical with
    or without progress streaming. If the disk is full, the progress file
    is unwritable, or the directory is read-only, the cycle must still
    complete normally.
    """
    bad = tmp_path / "does-not-exist" / "and-cannot-be-created"
    # Make the parent unwritable so mkdir(parents=True) fails.
    (tmp_path / "does-not-exist").mkdir()
    os.chmod(tmp_path / "does-not-exist", 0o400)
    try:
        emitter = ProgressEmitter(state_dir=bad)
        emitter.emit("cycle_start", tick_id="t_unwritable")
        emitter.emit("cycle_end", status="ok")
    finally:
        os.chmod(tmp_path / "does-not-exist", 0o700)
    # No exception raised; that's the whole assertion.
