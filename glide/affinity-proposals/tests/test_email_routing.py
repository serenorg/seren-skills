from __future__ import annotations

from scripts.email_send import EmailConfig, build_proposal_email


def test_dry_run_email_never_includes_live_owner():
    email = build_proposal_email(
        prospect_name="Acme Capital",
        contact_date="2026-06-01",
        owner_email="owner@example.com",
        config=EmailConfig(
            dry_run_to="dry-run@example.com",
            dry_run_cc=["review@example.com"],
            live_cc=["leader@example.com"],
        ),
        dry_run=True,
        attachment_name="acme.pdf",
        attachment_bytes=b"%PDF-test",
    )

    recipients = set(email.to + email.cc)
    assert email.to == ["dry-run@example.com"]
    assert email.cc == ["review@example.com"]
    assert "owner@example.com" not in recipients
    assert email.subject == "Proposal for Acme Capital after Contact 2026-06-01"
    assert "created one for you" in email.body


def test_live_email_routes_to_owner_and_configured_ccs():
    email = build_proposal_email(
        prospect_name="Acme Capital",
        contact_date="2026-06-01",
        owner_email="owner@example.com",
        config=EmailConfig(
            dry_run_to="dry-run@example.com",
            dry_run_cc=["review@example.com"],
            live_cc=["leader@example.com"],
        ),
        dry_run=False,
        attachment_name="acme.pdf",
        attachment_bytes=b"%PDF-test",
    )

    assert email.to == ["owner@example.com"]
    assert email.cc == ["leader@example.com"]
