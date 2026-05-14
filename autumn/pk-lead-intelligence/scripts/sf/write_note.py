"""Write the rendered enrichment Note to a Lead's Related tab (Phase 4).

The only write path to Lead records this skill exposes. Two gates
run before the Playwright form-drive fires:

* **Cross-division gate.** `is_packaging_lead(lead)` must be True.
  This is the P0 mis-routing defense documented in SKILL.md — a
  Note that lands on a non-PK Lead is a customer-confidential leak,
  not a cosmetic bug.
* **Recency gate.** The cron may tick the same Lead twice per day;
  if `Last_Enrichment_At__c` is within `skip_if_within_seconds`
  of the current time, the function returns `skipped_recent`
  without writing a duplicate.

The Playwright drivers (`_read_last_enrichment_at`,
`_drive_new_note_form`, `_update_last_enrichment_at`) are
`pragma: no cover` — live correctness is validated at the Phase 4
operator checkpoint, same pattern as Phases 1/2/3.

Order is load-bearing: the Note is written first, then the
timestamp is updated. If the order is reversed and the form-drive
raises, the timestamp lies about a Note that does not exist —
the cron will skip the next tick and the operator never sees the
Lead get its Note. Drive the Note, then stamp.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Protocol

from scripts.output.note_renderer import RenderedNote
from scripts.sf.client import LeadRow
from scripts.sf.enrich_lead import is_packaging_lead


# --------------------------------------------------------------------- #
# Protocols                                                             #
# --------------------------------------------------------------------- #


class _Page(Protocol):
    """Subset of `playwright.sync_api.Page` the driver uses."""

    url: str

    def goto(self, url: str, *, timeout: int = ...) -> object: ...


# --------------------------------------------------------------------- #
# Inputs / outputs                                                      #
# --------------------------------------------------------------------- #


# 24 hours. The cron runs daily at 06:00; a 24h window guarantees
# the same Lead is never enriched twice in the same business day.
_DEFAULT_SKIP_WINDOW_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class NoteWriteOptions:
    """Inputs to a single Note-write attempt.

    `lead.is_packaging` must be True for the write to proceed. The
    caller is responsible for populating it from the All Sources
    PK Leads report column.
    """

    lead: LeadRow
    note: RenderedNote
    skip_if_within_seconds: int = _DEFAULT_SKIP_WINDOW_SECONDS


@dataclass(frozen=True)
class NoteWriteResult:
    """Outcome of one `write_note_to_lead` call.

    `status` is one of:

    * `written` — Note was driven to completion and the timestamp
      was updated.
    * `skipped_non_pk` — cross-division gate refused the write.
    * `skipped_recent` — the Lead was enriched within the recency
      window; no new Note was written.
    * `dry_run` — caller passed `dry_run=True`; the function
      surfaced what it would have done without touching the form.

    `last_enriched_at` is ISO-8601 UTC. For `written` it is the
    `now` the caller supplied; for `skipped_recent` it is the
    value read off the Lead; for `skipped_non_pk` it is None.
    """

    status: str
    last_enriched_at: Optional[str]


# --------------------------------------------------------------------- #
# UI driving (validated at operator checkpoint)                          #
# --------------------------------------------------------------------- #


def _read_last_enrichment_at(  # pragma: no cover
    page: _Page,
    lead: LeadRow,
) -> Optional[datetime]:
    """Return the Lead's current `Last_Enrichment_At__c`, or None.

    Live execution navigates to the Lead detail page and reads the
    custom-field value off the Highlights panel. Tests monkeypatch
    this seam to model recent vs. stale Leads without driving
    Lightning.
    """

    raise NotImplementedError(
        "Live Last_Enrichment_At__c read is validated at the Phase 4 "
        "operator checkpoint."
    )


def _drive_new_note_form(  # pragma: no cover
    page: _Page,
    options: NoteWriteOptions,
) -> None:
    """Drive Lead Detail → Related → Notes → New Note.

    Live execution clicks the Notes related-list "New" button,
    fills title + body from `options.note`, and clicks Save.
    Tests monkeypatch this seam.
    """

    raise NotImplementedError(
        "Live Note-form driving is validated at the Phase 4 operator "
        "checkpoint."
    )


def _update_last_enrichment_at(  # pragma: no cover
    page: _Page,
    lead: LeadRow,
    when: datetime,
) -> None:
    """Set `Last_Enrichment_At__c` on the Lead to `when`.

    Live execution clicks the Highlights pencil-icon for the field
    and types the ISO-8601 string. Tests monkeypatch this seam.
    """

    raise NotImplementedError(
        "Live Last_Enrichment_At__c update is validated at the Phase 4 "
        "operator checkpoint."
    )


# --------------------------------------------------------------------- #
# Public surface                                                        #
# --------------------------------------------------------------------- #


def write_note_to_lead(
    *,
    page: _Page,
    options: NoteWriteOptions,
    now: datetime,
    dry_run: bool,
) -> NoteWriteResult:
    """Write the rendered Note to the Lead's Related tab.

    Behaviour matrix:

    1. `is_packaging_lead(lead) is False` → `skipped_non_pk`. No
       UI driving. P0 cross-division defense.
    2. `Last_Enrichment_At__c` is within `skip_if_within_seconds`
       → `skipped_recent`. No UI driving.
    3. `dry_run is True` → `dry_run`. No UI driving. The Lead's
       recency is surfaced so the operator's daily summary
       reflects the would-be skip.
    4. Otherwise → drive the New Note form, then update
       `Last_Enrichment_At__c`. Return `written`.
    """

    if not is_packaging_lead(options.lead):
        return NoteWriteResult(status="skipped_non_pk", last_enriched_at=None)

    last_at = _read_last_enrichment_at(page, options.lead)
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

    # Drive the Note first; only stamp the timestamp after the Note
    # write succeeds. If the order is reversed and the drive
    # raises, the timestamp lies about a Note that does not exist.
    _drive_new_note_form(page, options)
    _update_last_enrichment_at(page, options.lead, now)

    return NoteWriteResult(status="written", last_enriched_at=now.isoformat())
