"""Unit tests for scripts/output/batch_progress.py (issue #850).

The batch-observability fix has one load-bearing primitive: a progress
sink that, on every event, (a) prints a flushed human line so a
redirected log populates live instead of block-buffering until exit,
and (b) appends one JSON object to a run-log file *immediately*, so a
batch killed mid-run still leaves an accurate record of every lead
touched — including any Note already written.
"""

from __future__ import annotations

import json
from io import StringIO

from scripts.output import batch_progress


def test_progress_emits_flushed_human_line_and_incremental_jsonl(tmp_path):
    stream = StringIO()
    jsonl = tmp_path / "runs" / "batch_progress.jsonl"
    prog = batch_progress.BatchProgress(stream=stream, jsonl_path=jsonl)

    prog.lead_start(idx=1, total=2, record_id="00Q1", name="Alpha")

    # Interrupt-survivability: the JSONL must already hold lead 1 before
    # lead 2 is ever processed. A killed run is reconstructable from this.
    assert len(jsonl.read_text().splitlines()) == 1

    prog.lead_done(
        idx=1, total=2, record_id="00Q1", name="Alpha", status="written"
    )
    prog.lead_start(idx=2, total=2, record_id="00Q2", name="Beta")
    prog.lead_done(
        idx=2,
        total=2,
        record_id="00Q2",
        name="Beta",
        status="skipped_non_pk",
    )

    # Human stream: a visible line per event with ids + outcomes.
    text = stream.getvalue()
    assert "[1/2] 00Q1 Alpha" in text
    assert "NOTE WRITTEN" in text
    assert "[2/2] 00Q2 Beta" in text
    assert "skipped (not Packaging)" in text

    # JSONL audit: one parseable object per event, outcomes recorded.
    records = [json.loads(ln) for ln in jsonl.read_text().splitlines()]
    assert [r["event"] for r in records] == [
        "lead_start",
        "lead_done",
        "lead_start",
        "lead_done",
    ]
    done = [r for r in records if r["event"] == "lead_done"]
    assert done[0]["record_id"] == "00Q1"
    assert done[0]["status"] == "written"
    assert done[1]["status"] == "skipped_non_pk"
    assert all("ts" in r for r in records)


def test_null_progress_is_a_silent_no_op(tmp_path):
    """The default sink for callers that don't opt in must never raise
    and must never emit — existing dry/live unit tests rely on it."""

    null = batch_progress.NULL
    null.lead_start(idx=1, total=1, record_id="x", name="y")
    null.lead_done(idx=1, total=1, record_id="x", name="y", status="written")
    null.pause(idx=1, total=2, seconds=90)
