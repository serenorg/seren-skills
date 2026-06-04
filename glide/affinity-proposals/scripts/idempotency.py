from __future__ import annotations

from scripts.affinity import Note, has_prior_proposal_note
from scripts.audit import AuditLedger


def should_skip_prospect(
    *,
    prospect_id: str,
    mode: str,
    notes: list[Note],
    audit: AuditLedger,
) -> str | None:
    if mode == "live" and has_prior_proposal_note(notes):
        return "proposal already present in Affinity notes"
    if mode == "dry-run" and audit.proposal_exists(prospect_id, mode):
        return "proposal already generated in dry-run ledger"
    return None
