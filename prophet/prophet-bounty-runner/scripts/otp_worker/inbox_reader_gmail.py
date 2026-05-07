"""Gmail-publisher backend for InboxReader.

Reads the latest unread message matching a sender filter, decodes the
base64url-encoded body, and returns plain text.

Wire format: calls go through the agent's gateway at
  GET /users/me/messages?q=...
  GET /users/me/messages/{id}?format=full

The gateway is responsible for x402 / SEREN_API_KEY / OAuth-passthrough
plumbing; this reader just shapes the request.
"""

from __future__ import annotations

import base64
from datetime import datetime
from typing import Any

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
                "/users/me/messages",
                body=None,
                headers={"X-Gmail-Query": query},
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
            f"/users/me/messages/{msg_id}",
            body=None,
            headers={"X-Gmail-Format": "full"},
        )
        payload = (detail or {}).get("payload") or {}
        return _flatten_body(payload) or None
