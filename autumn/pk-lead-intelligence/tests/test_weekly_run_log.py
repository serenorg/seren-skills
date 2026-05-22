"""Critical test for scripts/storage/weekly_run_log.py.

The log is what `/pk-status` reads to surface the latest weekly doc.
One round-trip test pins the contract: `append` writes a JSONL record
and `latest` returns the most recent. Older records survive but never
shadow newer ones.
"""

from __future__ import annotations

from pathlib import Path

from scripts.storage import weekly_run_log


def test_append_then_latest_returns_most_recent(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"

    weekly_run_log.append(
        state_dir,
        {
            "week_label": "2026-W20",
            "title": "PK Weekly Status — 2026-W20",
            "doc_url": "https://docs.google.com/document/d/older",
            "shared_with": "nathan@example.com",
            "status": "shared",
            "generated_at_utc": "2026-05-12T07:00:00Z",
        },
    )
    weekly_run_log.append(
        state_dir,
        {
            "week_label": "2026-W21",
            "title": "PK Weekly Status — 2026-W21",
            "doc_url": "https://docs.google.com/document/d/newer",
            "shared_with": "nathan@example.com",
            "status": "shared",
            "generated_at_utc": "2026-05-19T07:00:00Z",
        },
    )

    latest = weekly_run_log.latest(state_dir)

    assert latest is not None
    assert latest["week_label"] == "2026-W21"
    assert latest["doc_url"] == "https://docs.google.com/document/d/newer"


def test_latest_returns_none_when_file_missing(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"  # not created
    assert weekly_run_log.latest(state_dir) is None
