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

    def preflight(self, sender_address: str = "") -> Any:
        """Verify the microsoft-outlook OAuth connection is live before sending.

        Both dry-run and live send from the connected mailbox via `/me/sendMail`
        (the operator connects the Seren `sender_address` at setup, so the send
        always originates from that mailbox). The publisher runs a `default_deny`
        allowlist with no identity endpoint, so this checks connection liveness
        with an *allowed* read endpoint and fails fast on a missing/expired
        connection; it cannot assert the exact mailbox address.

        A genuine auth/consent failure raises `SetupBlocked`. An allowlist
        "forbidden endpoint" 403 is never mislabeled as OAuth — it is re-raised.
        """

        from scripts.proposal import SetupBlocked
        from scripts.seren_client import PublisherError

        try:
            return self.gateway.call_publisher(
                "microsoft-outlook", method="GET", path="/me/mailFolders?$top=1"
            )
        except PublisherError as exc:
            if self._is_auth_error(exc):
                target = sender_address or "the Seren sender mailbox"
                raise SetupBlocked(
                    "Microsoft OAuth connection required for the Outlook sender account. "
                    f"Connect {target} to the microsoft-outlook publisher before sending — "
                    "both dry-run and live send from this mailbox until MS Publisher "
                    "Verification is complete."
                ) from exc
            raise

    @staticmethod
    def _is_auth_error(exc: Any) -> bool:
        # A 401, or a 403 that names OAuth, is an auth/consent failure. A 403 that
        # reports a forbidden/not-allowed endpoint is NOT — re-raise it as-is so a
        # publisher allowlist gap is never reported as "OAuth required" (#935).
        message = str(exc).lower()
        if "allowed endpoints" in message or "not in the allowed" in message:
            return False
        return getattr(exc, "status", None) in (401, 403) or "oauth" in message

    def send(self, email: ProposalEmail) -> Any:
        attachment = {
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": email.attachment_name,
            "contentType": (
                "application/vnd.openxmlformats-officedocument."
                "presentationml.presentation"
            ),
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
