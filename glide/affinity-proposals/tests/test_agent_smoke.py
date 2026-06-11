from __future__ import annotations

from datetime import date

from scripts.agent import AgentConfig, AgentServices, run_once
from scripts.affinity import AffinityScanSummary, Note, Prospect
from scripts.extract import ProposalProfile
from scripts.proposal import ProposalArtifact


class FakeAffinity:
    def __init__(self) -> None:
        self.write_calls: list[str] = []

    def qualified_prospects(self):
        return [
            Prospect(
                prospect_id="prospect-1",
                org_id="org-1",
                name="Acme Capital",
                status="Engaged - 25%",
                owner_email="owner@example.com",
                contact_date="2026-06-01",
                notes=[
                    Note(
                        content=(
                            "Met with Acme Capital to discuss a new fund launch, "
                            "target investors, service providers, timing, and "
                            "materials needed for a next-step deck."
                        )
                    )
                ],
            )
        ]

    def add_note(self, org_id, content):
        self.write_calls.append("add_note")

    def set_status(self, field_value_id, status_option_id):
        self.write_calls.append("set_status")


class FakeExtractor:
    def extract(self, note_text, org_name):
        return ProposalProfile(
            client_name=org_name,
            description="Synthetic profile.",
            seeking=["feeder funds"],
            structure="offshore",
            fund_name="Acme Credit Fund",
            advisor_name="Acme Advisors",
        )


class FakeProposal:
    def build(self, profile, today):
        return ProposalArtifact(
            pptx_path=None,
            pdf_bytes=b"%PDF-test",
            file_name="acme.pdf",
            template_used=None,
        )


class FakeEmailer:
    def __init__(self) -> None:
        self.sent = []

    def send(self, email):
        self.sent.append(email)
        return {"id": "message-1"}


def test_dry_run_orchestrator_generates_sends_audits_and_never_writes_live():
    affinity = FakeAffinity()
    emailer = FakeEmailer()
    services = AgentServices(
        affinity=affinity,
        extractor=FakeExtractor(),
        proposal=FakeProposal(),
        emailer=emailer,
    )

    summary = run_once(
        AgentConfig(
            dry_run=True,
            live_mode=False,
            dry_run_to="dry-run@example.com",
            dry_run_cc=["review@example.com"],
            live_cc=["leader@example.com"],
        ),
        services=services,
        today=date(2026, 6, 4),
    )

    assert summary.scanned == 1
    assert summary.generated == 1
    assert summary.sent == 1
    assert summary.written_back == 0
    assert affinity.write_calls == []
    assert emailer.sent[0].to == ["dry-run@example.com"]


def test_run_once_blocks_when_outlook_oauth_missing():
    import pytest

    from scripts.email_send import OutlookEmailSender
    from scripts.proposal import SetupBlocked
    from scripts.seren_client import PublisherError

    class DisconnectedGateway:
        def call_publisher(self, publisher, *, method="GET", path="/", **kwargs):
            raise PublisherError(401, "OAuthRequired: provider 'microsoft'")

    affinity = FakeAffinity()
    services = AgentServices(
        affinity=affinity,
        extractor=FakeExtractor(),
        proposal=FakeProposal(),
        emailer=OutlookEmailSender(DisconnectedGateway()),
    )

    with pytest.raises(SetupBlocked):
        run_once(
            AgentConfig(
                dry_run=True,
                live_mode=False,
                dry_run_to="dry-run@example.com",
                sender_address="taariq@serendb.com",
            ),
            services=services,
            today=date(2026, 6, 5),
        )

    # Preflight must block before any prospect work or write-back.
    assert affinity.write_calls == []


def test_run_once_surfaces_affinity_scan_skip_counts():
    class EmptyAffinity:
        def __init__(self) -> None:
            self.scan_summary = AffinityScanSummary(
                scanned_raw_count=1,
                skipped={"no_notes_via_api": 1},
            )

        def qualified_prospects(self):
            return []

    services = AgentServices(
        affinity=EmptyAffinity(),
        extractor=FakeExtractor(),
        proposal=FakeProposal(),
        emailer=FakeEmailer(),
    )

    summary = run_once(
        AgentConfig(dry_run=True, live_mode=False),
        services=services,
        today=date(2026, 6, 11),
    )

    assert summary.scanned == 1
    assert summary.skipped["no_notes_via_api"] == 1
