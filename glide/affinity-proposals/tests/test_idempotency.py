from __future__ import annotations

from scripts.affinity import Note
from scripts.audit import InMemoryAuditLedger
from scripts.idempotency import should_skip_prospect


def test_dry_run_uses_ledger_to_skip_second_send():
    ledger = InMemoryAuditLedger()

    assert (
        should_skip_prospect(
            prospect_id="prospect-1",
            mode="dry-run",
            notes=[Note(content="Long meeting summary without the key term." * 4)],
            audit=ledger,
        )
        is None
    )

    ledger.record_proposal(
        prospect_id="prospect-1",
        mode="dry-run",
        artifact_name="acme.pdf",
        request_key="prospect-1:dry-run:2026-06-04",
    )

    assert (
        should_skip_prospect(
            prospect_id="prospect-1",
            mode="dry-run",
            notes=[Note(content="Long meeting summary without the key term." * 4)],
            audit=ledger,
        )
        == "proposal already generated in dry-run ledger"
    )


def test_live_uses_affinity_proposal_note_to_skip_second_send():
    reason = should_skip_prospect(
        prospect_id="prospect-1",
        mode="live",
        notes=[Note(content="Proposal generated and emailed to owner@example.com")],
        audit=InMemoryAuditLedger(),
    )

    assert reason == "proposal already present in Affinity notes"
