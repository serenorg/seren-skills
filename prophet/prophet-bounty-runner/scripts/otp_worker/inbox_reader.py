"""InboxReader interface + factory.

Both gmail and outlook readers expose a single method:

    read_latest_otp_email(*, sender_filter: str, since: datetime) -> str | None

It returns the body text of the newest matching message, or None if no
match is present yet. Pagination/filtering is the reader's job;
TokenAcquirer just polls until a body comes back or the timeout expires.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from . import EmailPublisherUnavailable


class InboxReader(Protocol):
    """Provider-agnostic inbox interface."""

    def read_latest_otp_email(
        self, *, sender_filter: str, since: datetime
    ) -> str | None:  # pragma: no cover - Protocol
        ...


def make_inbox_reader(provider: str, *, gateway) -> InboxReader:
    """Factory: pick gmail or outlook based on inputs.email_provider."""
    if provider == "gmail":
        from .inbox_reader_gmail import GmailInboxReader

        return GmailInboxReader(gateway=gateway)
    if provider == "outlook":
        from .inbox_reader_outlook import OutlookInboxReader

        return OutlookInboxReader(gateway=gateway)
    raise EmailPublisherUnavailable(f"unsupported email_provider {provider!r}")
