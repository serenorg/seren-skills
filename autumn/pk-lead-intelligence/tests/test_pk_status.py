"""Critical tests for scripts/slash/pk_status.py.

Two paths, one test each:

  1. Latest record matches the current ISO week → return URL block.
  2. No record for the current week → return on-demand offer.

The ISO-week computation reads `now()` so tests inject a fixed
timestamp via the module's `_now` seam.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

import scripts.slash.pk_status as pk_status
from scripts.storage import weekly_run_log


def test_returns_latest_url_when_current_week_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    state_dir = tmp_path / "state"
    weekly_run_log.append(
        state_dir,
        {
            "week_label": "2026-W21",
            "title": "PK Weekly Status — 2026-W21",
            "doc_url": "https://docs.google.com/document/d/abc",
            "shared_with": "nathan@example.com",
            "status": "shared",
            "generated_at_utc": "2026-05-19T11:05:00Z",
        },
    )

    # Anchor "now" to a Wednesday inside ISO 2026-W21 so the latest record
    # is considered current.
    monkeypatch.setattr(
        pk_status,
        "_now_utc",
        lambda: datetime(2026, 5, 20, 14, 0, 0, tzinfo=timezone.utc),
    )

    exit_code = pk_status.main(["--state-dir", str(state_dir)])
    assert exit_code == 0

    out = capsys.readouterr().out
    assert "Latest: PK Weekly Status — 2026-W21" in out
    assert "https://docs.google.com/document/d/abc" in out
    assert "Shared with: nathan@example.com" in out


def test_offers_on_demand_run_when_current_week_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    state_dir = tmp_path / "state"
    weekly_run_log.append(
        state_dir,
        {
            "week_label": "2026-W19",
            "title": "PK Weekly Status — 2026-W19",
            "doc_url": "https://docs.google.com/document/d/old",
            "shared_with": "nathan@example.com",
            "status": "shared",
            "generated_at_utc": "2026-05-05T11:05:00Z",
        },
    )

    monkeypatch.setattr(
        pk_status,
        "_now_utc",
        lambda: datetime(2026, 5, 20, 14, 0, 0, tzinfo=timezone.utc),
    )

    exit_code = pk_status.main(["--state-dir", str(state_dir)])
    assert exit_code == 0

    out = capsys.readouterr().out
    assert "No weekly doc for the current week" in out
    assert "python scripts/agent.py --command weekly --allow-live" in out


def test_handles_empty_log_without_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    state_dir = tmp_path / "state"  # no log file

    monkeypatch.setattr(
        pk_status,
        "_now_utc",
        lambda: datetime(2026, 5, 20, 14, 0, 0, tzinfo=timezone.utc),
    )

    exit_code = pk_status.main(["--state-dir", str(state_dir)])
    assert exit_code == 0

    out = capsys.readouterr().out
    assert "No weekly doc for the current week" in out
