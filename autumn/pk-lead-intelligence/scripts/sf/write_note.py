"""Write the rendered enrichment Note to a Lead's Related tab (Phase 4).

The only write path to Lead records this skill exposes. Two gates
run before the Playwright form-drive fires:

* **Cross-division gate.** `is_packaging_lead(lead)` must be True.
  This is the P0 mis-routing defense documented in SKILL.md — a
  Note that lands on a non-PK Lead is a customer-confidential leak,
  not a cosmetic bug. The flag is populated by
  `client.populate_is_packaging` before this function is invoked.
* **Recency gate.** The cron may tick the same Lead twice per day;
  if the SerenDB-backed `EnrichmentLedger` reports a previous
  enrichment within `skip_if_within_seconds` of the current time,
  the function returns `skipped_recent` without writing a duplicate.

Order is load-bearing: the Note is written first, then the ledger
is updated. If the order is reversed and the form-drive raises, the
timestamp lies about a Note that does not exist — the cron will
skip the next tick and the operator never sees the Lead get its
Note. Drive the Note, then stamp.

Selectors were captured live against `herrmannultraschall.lightning
.force.com` on 2026-05-21 (issue #563). Lightning's modern Notes
related list is internally named `AttachedContentNotes` — that is
the modern `ContentNote` join object, not legacy Notes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Protocol

from scripts.output.note_renderer import RenderedNote
from scripts.sf import selectors
from scripts.sf.client import LeadRow
from scripts.sf.enrich_lead import is_packaging_lead
from scripts.storage.enrichment_ledger import EnrichmentLedger


# --------------------------------------------------------------------- #
# Protocols                                                             #
# --------------------------------------------------------------------- #


class _Locator(Protocol):
    """Subset of `playwright.sync_api.Locator` the form driver uses."""

    def click(self, *, timeout: int = ...) -> object: ...
    def fill(self, value: str, *, timeout: int = ...) -> object: ...
    def press_sequentially(self, text: str, *, delay: float = ...) -> object: ...


class _Page(Protocol):
    """Subset of `playwright.sync_api.Page` the driver uses."""

    url: str

    def goto(self, url: str, *, timeout: int = ...) -> object: ...
    def wait_for_selector(self, selector: str, *, timeout: int = ...) -> object: ...
    def click(self, selector: str, *, timeout: int = ...) -> object: ...
    def fill(self, selector: str, value: str, *, timeout: int = ...) -> object: ...
    def locator(self, selector: str) -> _Locator: ...


# --------------------------------------------------------------------- #
# Inputs / outputs                                                      #
# --------------------------------------------------------------------- #


# 24 hours. The cron runs daily at 06:00; a 24h window guarantees
# the same Lead is never enriched twice in the same business day.
_DEFAULT_SKIP_WINDOW_SECONDS = 24 * 60 * 60

# Generous timeouts — the Lead detail page and Note modal both
# hydrate asynchronously. The selectors are stable but the rendering
# can lag under load.
_DETAIL_NAV_TIMEOUT_MS = 30_000
_MODAL_OPEN_TIMEOUT_MS = 15_000
_SAVE_TIMEOUT_MS = 30_000


@dataclass(frozen=True)
class NoteWriteOptions:
    """Inputs to a single Note-write attempt.

    `lead.is_packaging` must be True for the write to proceed. The
    caller is responsible for populating it from the Lead detail
    page via `client.populate_is_packaging` before invoking this
    function.

    `salesforce_org_url` is needed so `_drive_new_note_form` can
    navigate to the Lead detail page from any starting URL.

    `agent_run_id` is an optional caller-supplied tag (e.g. a cron
    tick id) that lands in the ledger row for audit.
    """

    lead: LeadRow
    note: RenderedNote
    salesforce_org_url: str
    skip_if_within_seconds: int = _DEFAULT_SKIP_WINDOW_SECONDS
    agent_run_id: Optional[str] = None


@dataclass(frozen=True)
class NoteWriteResult:
    """Outcome of one `write_note_to_lead` call.

    `status` is one of:

    * `written` — Note was driven to completion and the ledger row
      was updated.
    * `skipped_non_pk` — cross-division gate refused the write.
    * `skipped_recent` — the ledger reported an enrichment within
      the recency window; no new Note was written.
    * `dry_run` — caller passed `dry_run=True`; the function
      surfaced what it would have done without touching the form.

    `last_enriched_at` is ISO-8601 UTC. For `written` it is the
    `now` the caller supplied; for `skipped_recent` it is the
    value read off the ledger; for `skipped_non_pk` it is None.
    """

    status: str
    last_enriched_at: Optional[str]


# --------------------------------------------------------------------- #
# UI driving                                                            #
# --------------------------------------------------------------------- #


def _drive_new_note_form(  # pragma: no cover
    page: _Page,
    options: NoteWriteOptions,
) -> None:
    """Drive Lead Detail → Related → Notes → New Note → fill → Done.

    Steps (verified live 2026-05-21 against HU's Lightning):

    1. Navigate to the Lead detail URL.
    2. Click the Related tab.
    3. Click "New" on the Notes (AttachedContentNotes) card.
    4. Wait for the Note modal.
    5. Fill the title input (placeholder "Untitled Note").
    6. Focus the Quill body editor (contenteditable div) and type
       the body. We use `press_sequentially` rather than `fill()`
       because Quill is not a regular textarea — `fill()` against a
       contenteditable raises in Playwright.
    7. Click Done. ContentNote auto-saves on type, so the note
       already exists when Done is clicked — Done finalizes and
       closes the modal.

    `pragma: no cover` — the unit tests monkeypatch this seam to
    avoid driving Playwright. Live correctness was verified at the
    operator checkpoint (issue #563).
    """

    base = options.salesforce_org_url.rstrip("/")
    detail_path = selectors.SF_LEAD_DETAIL_PATH_TEMPLATE.format(
        record_id=options.lead.record_id
    )
    page.goto(f"{base}{detail_path}", timeout=_DETAIL_NAV_TIMEOUT_MS)

    page.click(selectors.SF_LEAD_RELATED_TAB, timeout=_DETAIL_NAV_TIMEOUT_MS)
    page.click(
        selectors.SF_LEAD_NOTES_NEW_BUTTON, timeout=_DETAIL_NAV_TIMEOUT_MS
    )

    page.wait_for_selector(
        selectors.SF_NOTE_MODAL_TITLE_INPUT, timeout=_MODAL_OPEN_TIMEOUT_MS
    )

    page.fill(
        selectors.SF_NOTE_MODAL_TITLE_INPUT,
        options.note.title,
        timeout=_MODAL_OPEN_TIMEOUT_MS,
    )

    # Build the body as a single block of text. The renderer's
    # section headings + bodies join naturally — Quill renders the
    # double-newline as paragraph breaks.
    body_text = "\n\n".join(
        f"{section.heading}\n{section.body}" for section in options.note.sections
    )

    body_editor = page.locator(selectors.SF_NOTE_MODAL_BODY_EDITOR)
    body_editor.click(timeout=_MODAL_OPEN_TIMEOUT_MS)
    body_editor.press_sequentially(body_text, delay=10)

    page.click(selectors.SF_NOTE_MODAL_DONE_BUTTON, timeout=_SAVE_TIMEOUT_MS)


# --------------------------------------------------------------------- #
# Public surface                                                        #
# --------------------------------------------------------------------- #


def write_note_to_lead(
    *,
    page: _Page,
    options: NoteWriteOptions,
    now: datetime,
    dry_run: bool,
    ledger: EnrichmentLedger,
) -> NoteWriteResult:
    """Write the rendered Note to the Lead's Related tab.

    Behaviour matrix:

    1. `is_packaging_lead(lead) is False` → `skipped_non_pk`. No
       UI driving. P0 cross-division defense.
    2. The ledger reports an enrichment within
       `skip_if_within_seconds` → `skipped_recent`. No UI driving.
    3. `dry_run is True` → `dry_run`. No UI driving. The Lead's
       recency is surfaced so the operator's daily summary
       reflects the would-be skip.
    4. Otherwise → drive the New Note form, then record the
       enrichment in the ledger. Return `written`.
    """

    if not is_packaging_lead(options.lead):
        return NoteWriteResult(status="skipped_non_pk", last_enriched_at=None)

    last_at = ledger.read_last_enrichment(options.lead.record_id)
    if last_at is not None:
        elapsed = (now - last_at).total_seconds()
        if 0 <= elapsed < options.skip_if_within_seconds:
            return NoteWriteResult(
                status="skipped_recent",
                last_enriched_at=last_at.isoformat(),
            )

    if dry_run:
        return NoteWriteResult(
            status="dry_run",
            last_enriched_at=last_at.isoformat() if last_at else None,
        )

    # Drive the Note first; only stamp the ledger after the Note
    # write succeeds. If the order is reversed and the drive
    # raises, the ledger lies about a Note that does not exist.
    _drive_new_note_form(page, options)
    ledger.record_enrichment(
        lead_id=options.lead.record_id,
        when=now,
        note_title=options.note.title,
        agent_run_id=options.agent_run_id,
    )

    return NoteWriteResult(status="written", last_enriched_at=now.isoformat())
