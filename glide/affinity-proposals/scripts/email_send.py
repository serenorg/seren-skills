from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any


EMAIL_BODY = (
    "I saw that you completed the meeting with the contact and that your next "
    "step was to create a proposal. I went ahead and created one for you."
)


@dataclass
class EmailConfig:
    dry_run_to: str
    dry_run_cc: list[str] = field(default_factory=list)
    live_cc: list[str] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "EmailConfig":
        return cls(
            dry_run_to=str(data["dry_run_to"]),
            dry_run_cc=[str(item) for item in data.get("dry_run_cc", [])],
            live_cc=[str(item) for item in data.get("live_cc", [])],
        )


@dataclass
class ProposalEmail:
    to: list[str]
    cc: list[str]
    subject: str
    body: str
    attachment_name: str
    attachment_bytes: bytes


def build_proposal_email(
    *,
    prospect_name: str,
    contact_date: str,
    owner_email: str,
    config: EmailConfig,
    dry_run: bool,
    attachment_name: str,
    attachment_bytes: bytes,
) -> ProposalEmail:
    if dry_run:
        to = [config.dry_run_to]
        cc = list(config.dry_run_cc)
    else:
        to = [owner_email]
        cc = list(config.live_cc)

    return ProposalEmail(
        to=to,
        cc=cc,
        subject=f"Proposal for {prospect_name} after Contact {contact_date}",
        body=EMAIL_BODY,
        attachment_name=attachment_name,
        attachment_bytes=attachment_bytes,
    )


class OutlookEmailSender:
    def __init__(self, gateway: Any) -> None:
        self.gateway = gateway

    def preflight(self, sender_address: str) -> Any:
        """Assert the connected microsoft-outlook mailbox is the configured sender.

        Until MS Publisher Verification lands, both dry-run and live send from a
        fixed Seren-tenant mailbox (`/me/sendMail` sends from whichever mailbox is
        OAuth-connected). Refuse to send from any other connected mailbox — e.g. a
        customer's — so a misconnected or missing account fails fast before any
        proposal is generated or emailed.
        """

        from scripts.proposal import SetupBlocked
        from scripts.seren_client import PublisherError

        expected = sender_address.strip().lower()
        try:
            identity = self.gateway.call_publisher(
                "microsoft-outlook", method="GET", path="/me"
            )
        except PublisherError as exc:
            if getattr(exc, "status", None) in (401, 403) or "oauth" in str(exc).lower():
                raise SetupBlocked(
                    "Microsoft OAuth connection required for the Outlook sender account. "
                    f"Connect {sender_address} to the microsoft-outlook publisher before sending."
                ) from exc
            raise

        connected = ""
        if isinstance(identity, dict):
            connected = str(
                identity.get("mail") or identity.get("userPrincipalName") or ""
            ).strip()
        if not connected:
            raise SetupBlocked(
                "Could not determine the connected Outlook mailbox identity "
                "(microsoft-outlook /me returned no address)."
            )
        if connected.lower() != expected:
            raise SetupBlocked(
                f"Connected Outlook mailbox is '{connected}' but the configured sender is "
                f"'{sender_address}'. Connect the Seren sender mailbox to microsoft-outlook — "
                "both dry-run and live send from this mailbox until MS Publisher Verification "
                "is complete."
            )
        return identity

    def send(self, email: ProposalEmail) -> Any:
        attachment = {
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": email.attachment_name,
            "contentType": "application/pdf",
            "contentBytes": base64.b64encode(email.attachment_bytes).decode("ascii"),
        }
        body = {
            "message": {
                "subject": email.subject,
                "body": {"contentType": "Text", "content": email.body},
                "toRecipients": [{"emailAddress": {"address": item}} for item in email.to],
                "ccRecipients": [{"emailAddress": {"address": item}} for item in email.cc],
                "attachments": [attachment],
            },
            "saveToSentItems": True,
        }
        return self.gateway.call_publisher(
            "microsoft-outlook",
            method="POST",
            path="/me/sendMail",
            body=body,
        )
