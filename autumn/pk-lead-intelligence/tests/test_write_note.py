"""Critical tests for scripts/sf/write_note.py.

Phase 4 lights up the only write path to Lead records — the Note
write to the Related tab. The risks that matter:

* Mis-routed write (P0 per SKILL.md): a Note lands on a non-PK Lead.
* Re-enrichment loop: the cron writes a duplicate Note every tick
  because the recency gate is missing.

Both gates live in `write_note_to_lead`. The Playwright UI driver
is `pragma: no cover` — validated at the Phase 4 operator
checkpoint, same as the Phase 1/2/3 UI surfaces.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone

from scripts.output.note_renderer import NoteSection, RenderedNote
from scripts.sf import write_note
from scripts.sf.client import LeadRow


# --------------------------------------------------------------------- #
# Fakes                                                                 #
# --------------------------------------------------------------------- #


@dataclass
class FakePage:
    """Stand-in Page that records drive-call attempts."""

    call_log: list[tuple[str, tuple]] = field(default_factory=list)
    url: str = ""

    def goto(self, url, *, timeout: int = 0):
        self.call_log.append(("goto", (url,)))
        self.url = url


def _make_lead(*, is_packaging: bool) -> LeadRow:
    """Build a `LeadRow` flagged for or against the PK division.

    The `is_packaging` attribute is added by Phase 4 — `LeadRow`
    accepts it as a keyword via `dataclasses.replace` against a
    base row, exercising the cross-division gate's getattr default.
    """

    base = LeadRow(
        record_id="00Q5g00000XYZAbc",
        name="Acme GmbH",
        source_url="https://acme.lightning.force.com/lightning/o/Lead/list",
    )
    return replace(base, is_packaging=is_packaging) if hasattr(base, "is_packaging") else _attach(base, is_packaging)


def _attach(base: LeadRow, is_packaging: bool) -> LeadRow:
    """Fallback when LeadRow has not been extended yet.

    `is_packaging_lead` reads via `getattr` with a `False` default,
    so attaching the attribute on a fresh dataclass instance via
    object.__setattr__ is sufficient for the unit tests. The live
    path populates the field through `dataclasses.replace` on a
    LeadRow extended in Phase 4.
    """

    object.__setattr__(base, "is_packaging", is_packaging)
    return base


def _make_rendered_note() -> RenderedNote:
    return RenderedNote(
        title="PK Lead Enrichment — Acme GmbH",
        sections=[NoteSection(heading="Lead", body="Acme GmbH")],
        enriched_at_utc="2026-05-14T10:30:00Z",
    )


# --------------------------------------------------------------------- #
# Cross-division gate (P0)                                              #
# --------------------------------------------------------------------- #


def test_write_note_refuses_non_pk_lead(monkeypatch):
    """A Note must never land on a Lead outside the PK division.

    The gate is `is_packaging_lead(lead) is True`. When the flag
    is False, the function returns `status='skipped_non_pk'`
    without touching the Lead detail page. This is the P0
    mis-routing defense called out in SKILL.md.
    """

    page = FakePage()
    drive_calls: list[str] = []

    # If either Playwright driver is invoked on a non-PK Lead, the
    # gate has regressed. Spy on both seams.
    monkeypatch.setattr(
        write_note,
        "_read_last_enrichment_at",
        lambda *a, **k: drive_calls.append("read") or None,
    )
    monkeypatch.setattr(
        write_note,
        "_drive_new_note_form",
        lambda *a, **k: drive_calls.append("write"),
    )
    monkeypatch.setattr(
        write_note,
        "_update_last_enrichment_at",
        lambda *a, **k: drive_calls.append("update"),
    )

    result = write_note.write_note_to_lead(
        page=page,
        options=write_note.NoteWriteOptions(
            lead=_make_lead(is_packaging=False),
            note=_make_rendered_note(),
        ),
        now=datetime(2026, 5, 14, 10, 30, tzinfo=timezone.utc),
        dry_run=False,
    )

    assert result.status == "skipped_non_pk", (
        f"P0 mis-routing defense regressed: result={result}"
    )
    assert drive_calls == [], (
        f"Non-PK gate must short-circuit before any UI driving. "
        f"Driven: {drive_calls}"
    )


# --------------------------------------------------------------------- #
# Recency gate                                                          #
# --------------------------------------------------------------------- #


def test_write_note_skips_recently_enriched_lead(monkeypatch):
    """If `Last_Enrichment_At__c` is within `skip_if_within_seconds`,
    skip the write. The cron will otherwise re-enrich on every tick.
    """

    now = datetime(2026, 5, 14, 10, 30, tzinfo=timezone.utc)
    last_at = now - timedelta(hours=4)

    monkeypatch.setattr(
        write_note,
        "_read_last_enrichment_at",
        lambda page, lead: last_at,
    )

    drive_calls: list[str] = []
    monkeypatch.setattr(
        write_note,
        "_drive_new_note_form",
        lambda *a, **k: drive_calls.append("write"),
    )

    result = write_note.write_note_to_lead(
        page=FakePage(),
        options=write_note.NoteWriteOptions(
            lead=_make_lead(is_packaging=True),
            note=_make_rendered_note(),
        ),
        now=now,
        dry_run=False,
    )

    assert result.status == "skipped_recent"
    assert drive_calls == [], (
        f"Recently-enriched Lead must short-circuit. Driven: {drive_calls}"
    )
    assert result.last_enriched_at == last_at.isoformat()


def test_write_note_writes_when_last_enrichment_is_stale(monkeypatch):
    """Stale Last_Enrichment_At__c (older than the skip window) is
    fair game. Happy path: drive the form, update the timestamp,
    return `written`."""

    now = datetime(2026, 5, 14, 10, 30, tzinfo=timezone.utc)
    last_at = now - timedelta(days=2)

    monkeypatch.setattr(
        write_note,
        "_read_last_enrichment_at",
        lambda page, lead: last_at,
    )

    driven: list[str] = []
    monkeypatch.setattr(
        write_note,
        "_drive_new_note_form",
        lambda page, options: driven.append("write"),
    )
    monkeypatch.setattr(
        write_note,
        "_update_last_enrichment_at",
        lambda page, lead, when: driven.append(f"update:{when.isoformat()}"),
    )

    result = write_note.write_note_to_lead(
        page=FakePage(),
        options=write_note.NoteWriteOptions(
            lead=_make_lead(is_packaging=True),
            note=_make_rendered_note(),
        ),
        now=now,
        dry_run=False,
    )

    assert result.status == "written"
    assert result.last_enriched_at == now.isoformat()
    # write must precede update — if the update runs first and the
    # write fails, the timestamp lies about the Note that does not
    # exist. Order is load-bearing.
    assert driven == ["write", f"update:{now.isoformat()}"], driven


def test_write_note_dry_run_does_not_drive_ui(monkeypatch):
    """`dry_run=True` returns the plan without driving the form."""

    now = datetime(2026, 5, 14, 10, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(
        write_note,
        "_read_last_enrichment_at",
        lambda page, lead: None,
    )
    driven: list[str] = []
    monkeypatch.setattr(
        write_note,
        "_drive_new_note_form",
        lambda *a, **k: driven.append("write"),
    )
    monkeypatch.setattr(
        write_note,
        "_update_last_enrichment_at",
        lambda *a, **k: driven.append("update"),
    )

    result = write_note.write_note_to_lead(
        page=FakePage(),
        options=write_note.NoteWriteOptions(
            lead=_make_lead(is_packaging=True),
            note=_make_rendered_note(),
        ),
        now=now,
        dry_run=True,
    )

    assert result.status == "dry_run"
    assert driven == []
