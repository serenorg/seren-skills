"""Microsoft Outlook (Graph API) backend for InboxReader.

Wire format:
  GET /me/messages?$filter=...&$orderby=receivedDateTime desc&$top=1

Graph returns the body as `body.content` already decoded (HTML or text);
we strip basic HTML tags rather than pulling in a parser dependency,
since the OTP regex only needs to see the digit run.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from . import EmailPublisherUnavailable

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WS_RUN_RE = re.compile(r"[ \t]+")


def _strip_html(content: str) -> str:
    if not content:
        return ""
    text = _HTML_TAG_RE.sub(" ", content)
    text = _WS_RUN_RE.sub(" ", text)
    return text


class OutlookInboxReader:
    PUBLISHER = "microsoft-outlook"

    def __init__(self, *, gateway) -> None:
        self.gateway = gateway

    def read_latest_otp_email(
        self, *, sender_filter: str, since: datetime
    ) -> str | None:
        # Graph wants ISO 8601 with millisecond precision and a literal "Z".
        since_iso = since.strftime("%Y-%m-%dT%H:%M:%SZ")
        filter_clause = (
            f"from/emailAddress/address eq '{sender_filter}' "
            f"and isRead eq false and receivedDateTime ge {since_iso}"
        )
        try:
            listing: dict[str, Any] = self.gateway.call(
                self.PUBLISHER,
                "GET",
                "/me/messages",
                body=None,
                headers={
                    "X-Graph-Filter": filter_clause,
                    "X-Graph-OrderBy": "receivedDateTime desc",
                    "X-Graph-Top": "1",
                    "X-Graph-Select": "id,body,subject,receivedDateTime",
                },
            )
        except Exception as exc:  # pragma: no cover - live-only path
            raise EmailPublisherUnavailable(f"outlook listing failed: {exc}") from exc

        items = (listing or {}).get("value") or []
        if not items:
            return None
        body = (items[0].get("body") or {}).get("content") or ""
        if (items[0].get("body") or {}).get("contentType") == "html":
            body = _strip_html(body)
        return body or None
