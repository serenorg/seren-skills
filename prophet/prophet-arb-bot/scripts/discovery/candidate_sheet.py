"""Refresh the operator-facing arb-candidate sheet (#538).

Jill keeps the campaign spreadsheet (`prophet_arb_candidates.xlsx`)
open on her desk during the run. This module writes a refreshed copy
to ``state/arb_candidates.xlsx`` every cycle so she can see what the
bot just discovered + which side of the pairing flow each row is on.

Falls back to CSV if ``openpyxl`` isn't installed. The runtime adds
``openpyxl`` to ``requirements.txt`` so the xlsx path is the default.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from polymarket.discovery import PolymarketSource
from state_paths import resolve_state_dir


def _default_output_dir() -> Path:
    """Canonical state dir for the candidate sheet (issue #693).

    Both the run-progress emitter and this helper must agree on the
    state dir, or operators tail the wrong run_progress.jsonl and see
    silence while the bot is actually making progress.
    """
    return resolve_state_dir()


SHEET_HEADERS = [
    "Question",
    "Category",
    "Resolution ISO",
    "24h Volume ($)",
    "Polymarket Condition ID",
    "Polymarket URL",
    "Prophet Pair Status",
]

STATUS_PAIRED_THIS_RUN = "paired_this_run"
STATUS_ALREADY_PAIRED = "already_paired"
STATUS_PENDING_CREATION = "pending_prophet_creation"
STATUS_UNKNOWN = "unknown"


def _row_for(
    cand: PolymarketSource,
    *,
    auto_paired_ids: set[str],
    already_paired_ids: set[str],
    pending_ids: set[str],
) -> list:
    if cand.polymarket_market_id in auto_paired_ids:
        status = STATUS_PAIRED_THIS_RUN
    elif cand.polymarket_market_id in already_paired_ids:
        status = STATUS_ALREADY_PAIRED
    elif cand.polymarket_market_id in pending_ids:
        status = STATUS_PENDING_CREATION
    else:
        status = STATUS_UNKNOWN

    polymarket_url = (
        f"https://polymarket.com/event/{cand.slug}" if cand.slug else ""
    )
    return [
        cand.question,
        cand.category or "",
        cand.resolution_date.isoformat(),
        round(cand.volume_24h_usd, 2),
        cand.polymarket_market_id,
        polymarket_url,
        status,
    ]


def write_candidate_sheet(
    *,
    candidates: Iterable[PolymarketSource],
    auto_paired_ids: set[str],
    already_paired_ids: set[str],
    pending_ids: set[str],
    output_dir: Path | None = None,
) -> Path | None:
    """Write the candidate set with pair-status annotations.

    Returns the path written, or ``None`` if no candidates were
    surfaced this run (no sheet emitted; nothing to refresh).
    """
    candidates = list(candidates)
    if not candidates:
        return None

    if output_dir is None:
        output_dir = _default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)

    rows = [
        _row_for(
            cand,
            auto_paired_ids=auto_paired_ids,
            already_paired_ids=already_paired_ids,
            pending_ids=pending_ids,
        )
        for cand in candidates
    ]

    try:
        from openpyxl import Workbook  # type: ignore

        path = output_dir / "arb_candidates.xlsx"
        wb = Workbook()
        sh = wb.active
        sh.title = "Arb candidates"
        sh.append(SHEET_HEADERS)
        for row in rows:
            sh.append(row)
        sh.column_dimensions["A"].width = 70
        sh.column_dimensions["F"].width = 50
        sh.column_dimensions["G"].width = 24
        sh.freeze_panes = "A2"
        sh.auto_filter.ref = sh.dimensions
        wb.save(path)
        return path
    except ImportError:
        # openpyxl absent — fall back to CSV. Both formats open cleanly
        # in Numbers/Excel; CSV is just less pretty.
        import csv

        path = output_dir / "arb_candidates.csv"
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(SHEET_HEADERS)
            writer.writerows(rows)
        return path
