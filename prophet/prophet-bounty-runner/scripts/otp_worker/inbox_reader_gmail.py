"""Gmail-publisher backend for InboxReader.

Reads the latest unread message matching a sender filter, decodes the
base64url-encoded body, and returns plain text.

Wire format: the seren `gmail` publisher exposes the Google Gmail API
under a flat surface (per the seren-bucks skill doc: `gmail` is scoped
to `/messages`, `/threads`, `/drafts`). The publisher strips the
`/users/me/` prefix from the upstream path and routes by tool. Use the
plain `q=` query param, not the `X-Gmail-Query` header.

Phase-14 live probe (2026-05-08): `GET /publishers/gmail/messages?q=...`
returns 200; `GET /publishers/gmail/users/me/messages` returns 403.
"""

from __future__ import annotations

import base64
from datetime import datetime
from typing import Any
from urllib.parse import quote

from . import EmailPublisherUnavailable


def _epoch_seconds(dt: datetime) -> int:
    return int(dt.timestamp())


def _decode_b64url(data: str) -> str:
    if not data:
        return ""
    pad = "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(data + pad).decode("utf-8", errors="replace")
    except (ValueError, TypeError):
        return ""


def _flatten_body(payload: dict[str, Any]) -> str:
    """Walk a Gmail MIME payload tree and concatenate text/* parts."""
    if not isinstance(payload, dict):
        return ""
    mime = payload.get("mimeType") or ""
    body = payload.get("body") or {}
    data = body.get("data") if isinstance(body, dict) else None
    parts = payload.get("parts") or []

    if mime.startswith("text/") and data:
        return _decode_b64url(data)
    out: list[str] = []
    for part in parts:
        out.append(_flatten_body(part))
    return "\n".join(p for p in out if p)


class GmailInboxReader:
    PUBLISHER = "gmail"

    def __init__(self, *, gateway) -> None:
        self.gateway = gateway

    def read_latest_otp_email(
        self, *, sender_filter: str, since: datetime
    ) -> str | None:
        query = f"from:{sender_filter} is:unread after:{_epoch_seconds(since)}"
        try:
            listing = self.gateway.call(
                self.PUBLISHER,
                "GET",
                f"/messages?q={quote(query)}&maxResults=5",
                body=None,
            )
        except Exception as exc:  # pragma: no cover - exercised in live tests
            raise EmailPublisherUnavailable(f"gmail listing failed: {exc}") from exc

        messages = (listing or {}).get("messages") or []
        if not messages:
            return None
        # Gmail returns newest-first by default.
        msg_id = messages[0].get("id")
        if not msg_id:
            return None
        detail = self.gateway.call(
            self.PUBLISHER,
            "GET",
            f"/messages/{msg_id}?format=full",
            body=None,
        )
        payload = (detail or {}).get("payload") or {}
        return _flatten_body(payload) or None
