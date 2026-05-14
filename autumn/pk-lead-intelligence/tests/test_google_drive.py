"""Critical tests for scripts/integrations/google_drive.py.

Tests the share-gate: a missing `nathan_share_email` must not
silently publish-without-sharing. The publisher injection seam
keeps tests off the network.
"""

from __future__ import annotations

from scripts.integrations import google_drive
from scripts.output import weekly_status


def _make_doc() -> weekly_status.WeeklyStatusDoc:
    return weekly_status.WeeklyStatusDoc(
        title="PK Weekly Status — 2026-W19",
        body="body",
        lead_count=0,
        enrichment_count=0,
        week_window="2026-05-11 to 2026-05-17",
    )


def test_upload_and_share_skips_when_share_email_empty():
    """The skill must not publish a weekly doc without sharing it.

    `nathan_share_email` empty is operator misconfiguration —
    return `skipped_no_email` with no Drive call so the operator
    sees the gap in the daily summary rather than the doc landing
    in a folder no one watches.
    """

    publisher_calls: list[tuple[str, str, dict]] = []

    def fake_publisher_call(publisher: str, path: str, body: dict) -> dict:
        publisher_calls.append((publisher, path, body))
        return {}

    result = google_drive.upload_and_share(
        doc=_make_doc(),
        folder_id="folder123",
        share_email="",
        dry_run=False,
        publisher_call=fake_publisher_call,
    )

    assert result.status == "skipped_no_email"
    assert result.doc_url is None
    assert publisher_calls == [], (
        f"Empty share email must short-circuit before Drive upload. "
        f"Calls: {publisher_calls}"
    )


def test_upload_and_share_dry_run_does_not_call_publisher():
    """`dry_run=True` returns the plan without hitting the
    google-drive publisher."""

    publisher_calls: list[tuple[str, str, dict]] = []

    def fake_publisher_call(publisher: str, path: str, body: dict) -> dict:
        publisher_calls.append((publisher, path, body))
        return {}

    result = google_drive.upload_and_share(
        doc=_make_doc(),
        folder_id="folder123",
        share_email="nathan@example.com",
        dry_run=True,
        publisher_call=fake_publisher_call,
    )

    assert result.status == "dry_run"
    assert publisher_calls == []
    assert result.shared_with == "nathan@example.com"


def test_upload_and_share_uploads_then_shares():
    """Happy path: upload → share. Order is load-bearing. If share
    is called against an unuploaded file id, the publisher 404s
    and the operator has to chase the failure manually."""

    publisher_calls: list[tuple[str, str, dict]] = []

    def fake_publisher_call(publisher: str, path: str, body: dict) -> dict:
        publisher_calls.append((publisher, path, body))
        if path == "/files":
            return {"id": "fileXYZ", "webViewLink": "https://drive.example/d/fileXYZ"}
        if path.endswith("/permissions"):
            return {"id": "perm123"}
        return {}

    result = google_drive.upload_and_share(
        doc=_make_doc(),
        folder_id="folder123",
        share_email="nathan@example.com",
        dry_run=False,
        publisher_call=fake_publisher_call,
    )

    assert result.status == "shared"
    assert result.doc_url == "https://drive.example/d/fileXYZ"
    assert result.shared_with == "nathan@example.com"

    # Upload (POST /files) precedes share (POST /files/{id}/permissions).
    upload_idx = next(
        i for i, c in enumerate(publisher_calls) if c[1] == "/files"
    )
    share_idx = next(
        i for i, c in enumerate(publisher_calls)
        if c[1].endswith("/permissions")
    )
    assert upload_idx < share_idx, (
        f"Upload must precede share. Calls in order: "
        f"{[c[1] for c in publisher_calls]}"
    )
