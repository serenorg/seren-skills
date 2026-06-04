from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from scripts.audit import AuditLedger, InMemoryAuditLedger
from scripts.email_send import EmailConfig, build_proposal_email
from scripts.extract import (
    DEFAULT_MODEL,
    ExtractionConfig,
    GatewayModelClient,
    extract_profile,
)
from scripts.idempotency import should_skip_prospect


@dataclass
class AgentConfig:
    dry_run: bool = True
    live_mode: bool = False
    dry_run_to: str = "dry-run@example.com"
    dry_run_cc: list[str] = field(default_factory=list)
    live_cc: list[str] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "AgentConfig":
        email = data.get("email", {})
        return cls(
            dry_run=bool(data.get("dry_run", True)),
            live_mode=bool(data.get("live_mode", False)),
            dry_run_to=str(email.get("dry_run_to", "dry-run@example.com")),
            dry_run_cc=[str(item) for item in email.get("dry_run_cc", [])],
            live_cc=[str(item) for item in email.get("live_cc", [])],
        )


@dataclass
class AgentServices:
    affinity: Any
    extractor: Any
    proposal: Any
    emailer: Any
    audit: AuditLedger = field(default_factory=InMemoryAuditLedger)


class ExtractorService:
    def __init__(self, model_client: Any, config: ExtractionConfig) -> None:
        self.model_client = model_client
        self.config = config

    def extract(self, note_text: str, org_name: str):
        return extract_profile(
            note_text,
            org_name=org_name,
            model_client=self.model_client,
            config=self.config,
        )


@dataclass
class RunSummary:
    scanned: int = 0
    qualified: int = 0
    generated: int = 0
    sent: int = 0
    written_back: int = 0
    skipped: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scanned": self.scanned,
            "qualified": self.qualified,
            "generated": self.generated,
            "sent": self.sent,
            "written_back": self.written_back,
            "skipped": self.skipped,
        }


def run_once(config: AgentConfig, *, services: AgentServices, today: date) -> RunSummary:
    if not config.dry_run and not config.live_mode:
        raise RuntimeError("live run requires live_mode=true in config")

    mode = "dry-run" if config.dry_run else "live"
    summary = RunSummary()
    email_config = EmailConfig(
        dry_run_to=config.dry_run_to,
        dry_run_cc=config.dry_run_cc,
        live_cc=config.live_cc,
    )

    prospects = list(services.affinity.qualified_prospects())
    summary.scanned = len(prospects)
    for prospect in prospects:
        reason = should_skip_prospect(
            prospect_id=prospect.prospect_id,
            mode=mode,
            notes=prospect.notes,
            audit=services.audit,
        )
        if reason:
            summary.skipped[prospect.prospect_id] = reason
            continue

        summary.qualified += 1
        trigger_note = prospect.notes[0]
        profile = services.extractor.extract(trigger_note.content, prospect.name)
        artifact = services.proposal.build(profile, today)
        summary.generated += 1

        email = build_proposal_email(
            prospect_name=prospect.name,
            contact_date=prospect.contact_date,
            owner_email=prospect.owner_email,
            config=email_config,
            dry_run=config.dry_run,
            attachment_name=artifact.file_name,
            attachment_bytes=artifact.pdf_bytes,
        )
        send_result = services.emailer.send(email)
        message_id = str((send_result or {}).get("id") or "")
        services.audit.record_proposal(
            prospect_id=prospect.prospect_id,
            mode=mode,
            artifact_name=artifact.file_name,
            request_key=f"{prospect.prospect_id}:{mode}:{today:%Y-%m-%d}",
        )
        if hasattr(services.audit, "record_email"):
            services.audit.record_email(
                prospect_id=prospect.prospect_id,
                mode=mode,
                message_id=message_id,
            )
        summary.sent += 1

        if not config.dry_run:
            services.affinity.add_note(
                prospect.org_id,
                (
                    "Proposal generated and emailed to owner on "
                    f"{today:%Y-%m-%d} by Glide AI agent "
                    f"(glide-affinity-proposals). Structure: {profile.structure}."
                ),
            )
            if prospect.status_field_value_id and prospect.proposal_status_option_id:
                services.affinity.set_status(
                    prospect.status_field_value_id,
                    prospect.proposal_status_option_id,
                )
            summary.written_back += 1
    return summary


def _load_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_services(raw_config: dict[str, Any], *, skill_root: Path) -> AgentServices:
    from scripts.affinity import AffinityClient, AffinityProspectSource
    from scripts.audit import SerenDBAuditLedger
    from scripts.email_send import OutlookEmailSender
    from scripts.proposal import ProposalService, ProposalTemplatePaths, SharePointRenderer
    from scripts.secrets import SecretConfig, SecretResolver
    from scripts.serendb import SerenDBManager
    from scripts.seren_client import GatewayClient

    gateway = GatewayClient.from_env(skill_root=skill_root)
    secrets = SecretResolver(
        gateway,
        SecretConfig.from_mapping(raw_config.get("secrets", {})),
    )
    affinity_cfg = raw_config.get("affinity", {})
    affinity_client = AffinityClient(secrets.get_affinity_key())
    affinity_source = AffinityProspectSource(
        affinity_client,
        list_name=str(affinity_cfg["list_name"]),
        engaged_status=str(affinity_cfg.get("engaged_status", "Engaged - 25%")),
        proposal_status=str(affinity_cfg.get("proposal_status", "Proposal - 50%")),
    )

    templates = ProposalTemplatePaths(
        offshore=skill_root / "assets/templates/glide_proposal_offshore.pptx",
        onshore=skill_root / "assets/templates/glide_proposal_onshore.pptx",
    )
    proposal = ProposalService(
        templates=templates,
        renderer=SharePointRenderer(
            gateway,
            folder_name=str(raw_config.get("sharepoint", {}).get("folder_name", "AI Proposals")),
        ),
        output_dir=skill_root / "out",
    )
    serendb_cfg = raw_config.get("serendb", {})
    serendb_project = str(serendb_cfg.get("project", "glide-affinity-proposals"))
    serendb_database = str(serendb_cfg.get("database", "glide_affinity_proposals"))
    project_id, branch_id = SerenDBManager(gateway).ensure_project_database(
        project_name=serendb_project,
        database_name=serendb_database,
    )
    audit = SerenDBAuditLedger(
        gateway,
        project_id=project_id,
        branch_id=branch_id,
        database=serendb_database,
    )
    audit.ensure_schema()
    return AgentServices(
        affinity=affinity_source,
        extractor=ExtractorService(
            GatewayModelClient(
                gateway,
                model=str(raw_config.get("extract", {}).get("model") or DEFAULT_MODEL),
            ),
            ExtractionConfig(),
        ),
        proposal=proposal,
        emailer=OutlookEmailSender(gateway),
        audit=audit,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--allow-live", action="store_true")
    args = parser.parse_args()
    if not args.once:
        parser.error("only --once is implemented")

    raw = _load_config(Path(args.config))
    config = AgentConfig.from_mapping(raw)
    if args.allow_live:
        config.dry_run = False
    if not config.dry_run and not config.live_mode:
        raise RuntimeError("--allow-live also requires live_mode=true in config.json")

    skill_root = Path(__file__).resolve().parent.parent
    summary = run_once(
        config,
        services=build_services(raw, skill_root=skill_root),
        today=date.today(),
    )
    print(json.dumps(summary.to_dict(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
