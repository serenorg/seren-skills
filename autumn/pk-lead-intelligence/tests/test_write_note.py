"""Critical tests for scripts/sf/write_note.py.

Phase 4 lights up the only write path to Lead records — the Note
write to the Related tab. The risks that matter:

* Mis-routed write (P0 per SKILL.md): a Note lands on a non-PK Lead.
* Re-enrichment loop: the cron writes a duplicate Note every tick
  because the recency gate is missing.

Both gates live in `write_note_to_lead`. The Playwright UI driver
is `pragma: no cover` — selectors were verified live against HU's
Lightning on 2026-05-21 (issue #563).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Optional

from scripts.output.note_renderer import NoteSection, RenderedNote
from scripts.sf import write_note
from scripts.sf.client import LeadRow


# --------------------------------------------------------------------- #
# Fakes                                                                 #
# --------------------------------------------------------------------- #


@dataclass
class FakePage:
    """Stand-in Page that records drive-call attempts.

    The unit tests never expect _drive_new_note_form to be invoked
    against this fake — they monkeypatch the driver and assert on
    the spy list. The page here exists only so write_note_to_lead's
    signature accepts something.
    """

    call_log: list[tuple[str, tuple]] = field(default_factory=list)
    url: str = ""

    def goto(self, url, *, timeout: int = 0):
        self.call_log.append(("goto", (url,)))
        self.url = url


@dataclass
class FakeLedger:
    """In-memory `EnrichmentLedger` for unit tests.

    The ledger is a Protocol; this struct satisfies it. Tests seed
    `last_at` to model a previously-enriched Lead, and assert against
    `records` to confirm the write path stamped the row in the right
    order relative to the UI driver.
    """

    last_at: Optional[datetime] = None
    records: list[dict] = field(default_factory=list)

    def read_last_enrichment(self, lead_id: str) -> Optional[datetime]:
        return self.last_at

    def record_enrichment(
        self,
        *,
        lead_id: str,
        when: datetime,
        note_title: str,
        agent_run_id: Optional[str] = None,
    ) -> None:
        self.records.append(
            {
                "lead_id": lead_id,
                "when": when,
                "note_title": note_title,
                "agent_run_id": agent_run_id,
            }
        )


def _make_lead(*, is_packaging: bool) -> LeadRow:
    return LeadRow(
        record_id="00Q5g00000XYZAbc",
        name="Acme GmbH",
        source_url="https://acme.lightning.force.com/lightning/o/Lead/list",
        is_packaging=is_packaging,
    )


def _make_rendered_note() -> RenderedNote:
    return RenderedNote(
        title="PK Lead Enrichment — Acme GmbH",
        sections=[NoteSection(heading="Lead", body="Acme GmbH")],
        enriched_at_utc="2026-05-21T10:30:00Z",
    )


def _make_options(*, is_packaging: bool) -> write_note.NoteWriteOptions:
    return write_note.NoteWriteOptions(
        lead=_make_lead(is_packaging=is_packaging),
        note=_make_rendered_note(),
        salesforce_org_url="https://acme.lightning.force.com",
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
    ledger = FakeLedger()
    drive_calls: list[str] = []

    monkeypatch.setattr(
        write_note,
        "_drive_new_note_form",
        lambda *a, **k: drive_calls.append("write"),
    )

    result = write_note.write_note_to_lead(
        page=page,
        options=_make_options(is_packaging=False),
        now=datetime(2026, 5, 21, 10, 30, tzinfo=timezone.utc),
        dry_run=False,
        ledger=ledger,
    )

    assert result.status == "skipped_non_pk", (
        f"P0 mis-routing defense regressed: result={result}"
    )
    assert drive_calls == [], (
        f"Non-PK gate must short-circuit before any UI driving. "
        f"Driven: {drive_calls}"
    )
    assert ledger.records == [], (
        "Non-PK gate must not touch the ledger either — a stamped "
        "ledger row implies a Note that did not get written."
    )


def test_write_note_refuses_lead_with_none_is_packaging(monkeypatch):
    """`is_packaging=None` (not yet populated from the detail page)
    must fail closed via the same gate. Defense-in-depth: if the
    orchestrator forgets to call `populate_is_packaging`, the write
    must not fire — `bool(None) is False` keeps the gate honest.
    """

    page = FakePage()
    ledger = FakeLedger()
    drive_calls: list[str] = []
    monkeypatch.setattr(
        write_note,
        "_drive_new_note_form",
        lambda *a, **k: drive_calls.append("write"),
    )

    lead_with_no_classification = LeadRow(
        record_id="00Q",
        name="X",
        source_url="",
        is_packaging=None,
    )
    options = write_note.NoteWriteOptions(
        lead=lead_with_no_classification,
        note=_make_rendered_note(),
        salesforce_org_url="https://acme.lightning.force.com",
    )

    result = write_note.write_note_to_lead(
        page=page,
        options=options,
        now=datetime(2026, 5, 21, 10, 30, tzinfo=timezone.utc),
        dry_run=False,
        ledger=ledger,
    )

    assert result.status == "skipped_non_pk"
    assert drive_calls == []


# --------------------------------------------------------------------- #
# Recency gate (ledger-backed, issue #563)                              #
# --------------------------------------------------------------------- #


def test_write_note_skips_recently_enriched_lead(monkeypatch):
    """If the ledger reports an enrichment within
    `skip_if_within_seconds`, skip the write. Otherwise the cron
    would re-enrich on every tick and produce duplicate Notes."""

    now = datetime(2026, 5, 21, 10, 30, tzinfo=timezone.utc)
    last_at = now - timedelta(hours=4)
    ledger = FakeLedger(last_at=last_at)

    drive_calls: list[str] = []
    monkeypatch.setattr(
        write_note,
        "_drive_new_note_form",
        lambda *a, **k: drive_calls.append("write"),
    )

    result = write_note.write_note_to_lead(
        page=FakePage(),
        options=_make_options(is_packaging=True),
        now=now,
        dry_run=False,
        ledger=ledger,
    )

    assert result.status == "skipped_recent"
    assert drive_calls == [], (
        f"Recently-enriched Lead must short-circuit. Driven: {drive_calls}"
    )
    assert result.last_enriched_at == last_at.isoformat()
    assert ledger.records == [], (
        "Recency skip must not double-stamp the ledger."
    )


def test_write_note_writes_when_ledger_is_stale(monkeypatch):
    """Ledger row older than the skip window is fair game. Happy
    path: drive the form, stamp the ledger, return `written`."""

    now = datetime(2026, 5, 21, 10, 30, tzinfo=timezone.utc)
    last_at = now - timedelta(days=2)
    ledger = FakeLedger(last_at=last_at)

    driven: list[str] = []
    monkeypatch.setattr(
        write_note,
        "_drive_new_note_form",
        lambda page, options: driven.append("write"),
    )

    result = write_note.write_note_to_lead(
        page=FakePage(),
        options=_make_options(is_packaging=True),
        now=now,
        dry_run=False,
        ledger=ledger,
    )

    assert result.status == "written"
    assert result.last_enriched_at == now.isoformat()
    # write must precede ledger stamp — if the stamp runs first and
    # the write fails, the ledger lies about a Note that does not
    # exist. Order is load-bearing.
    assert driven == ["write"], driven
    assert len(ledger.records) == 1
    record = ledger.records[0]
    assert record["lead_id"] == "00Q5g00000XYZAbc"
    assert record["when"] == now
    assert record["note_title"] == "PK Lead Enrichment — Acme GmbH"


def test_write_note_writes_when_ledger_has_no_row(monkeypatch):
    """First-ever enrichment of a Lead: ledger returns None; happy
    path writes and creates the row."""

    now = datetime(2026, 5, 21, 10, 30, tzinfo=timezone.utc)
    ledger = FakeLedger(last_at=None)

    driven: list[str] = []
    monkeypatch.setattr(
        write_note,
        "_drive_new_note_form",
        lambda page, options: driven.append("write"),
    )

    result = write_note.write_note_to_lead(
        page=FakePage(),
        options=_make_options(is_packaging=True),
        now=now,
        dry_run=False,
        ledger=ledger,
    )

    assert result.status == "written"
    assert result.last_enriched_at == now.isoformat()
    assert driven == ["write"]
    assert len(ledger.records) == 1


def test_write_note_dry_run_does_not_drive_ui(monkeypatch):
    """`dry_run=True` returns the plan without driving the form OR
    touching the ledger."""

    now = datetime(2026, 5, 21, 10, 30, tzinfo=timezone.utc)
    ledger = FakeLedger(last_at=None)

    driven: list[str] = []
    monkeypatch.setattr(
        write_note,
        "_drive_new_note_form",
        lambda *a, **k: driven.append("write"),
    )

    result = write_note.write_note_to_lead(
        page=FakePage(),
        options=_make_options(is_packaging=True),
        now=now,
        dry_run=True,
        ledger=ledger,
    )

    assert result.status == "dry_run"
    assert driven == []
    assert ledger.records == [], (
        "Dry-run must not touch the ledger."
    )


def test_write_note_propagates_agent_run_id_to_ledger(monkeypatch):
    """The optional `agent_run_id` lands on the ledger row for
    audit. Phase 5 cron will set this to its tick id."""

    now = datetime(2026, 5, 21, 10, 30, tzinfo=timezone.utc)
    ledger = FakeLedger(last_at=None)

    monkeypatch.setattr(
        write_note,
        "_drive_new_note_form",
        lambda *a, **k: None,
    )

    options = replace(
        _make_options(is_packaging=True),
        agent_run_id="cron-tick-12345",
    )

    write_note.write_note_to_lead(
        page=FakePage(),
        options=options,
        now=now,
        dry_run=False,
        ledger=ledger,
    )

    assert len(ledger.records) == 1
    assert ledger.records[0]["agent_run_id"] == "cron-tick-12345"
