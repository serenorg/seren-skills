"""Seren Affiliates webhook client for knowledge skill reward events.

Fires HMAC-signed webhooks to seren-affiliates when employees earn
SerenBucks through knowledge capture or retrieval events.

Usage:
    from affiliates_webhook import fire_reward_webhook

    result = fire_reward_webhook(
        config=config,
        agent_id="user-123",
        referral_code="REF-ABC",
        event_type="knowledge_capture",
        amount_cents=100,
    )
"""

from __future__ import annotations

import hashlib
import hmac
import json
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import uuid4


def _sign_payload(payload_bytes: bytes, secret: str) -> str:
    """Compute HMAC-SHA256 signature for the webhook payload."""
    return hmac.new(
        secret.encode("utf-8"),
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()


def build_reward_payload(
    *,
    publisher_id: str,
    publisher_slug: str,
    agent_id: str,
    referral_code: str,
    amount_cents: int = 100,
    event_type: str = "knowledge_capture",
    test_mode: bool = False,
    transaction_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the webhook payload for a reward event.

    Args:
        publisher_id: Org slug (e.g., "rendero-trust")
        publisher_slug: Publisher slug for billing
        agent_id: User ID from authenticated session
        referral_code: User's affiliate referral code from session
        amount_cents: Notional value in cents (default 100 = $1.00)
        event_type: "knowledge_capture" or "knowledge_retrieval"
        test_mode: If True, seren-affiliates processes but does not pay out
        transaction_id: Unique ID; auto-generated if omitted

    Returns:
        Webhook payload dict
    """
    return {
        "transaction_id": transaction_id or str(uuid4()),
        "publisher_id": publisher_id,
        "publisher_slug": publisher_slug,
        "amount_cents": amount_cents,
        "agent_id": agent_id,
        "referral_code": referral_code,
        "event_type": event_type,
        "test_mode": test_mode,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def fire_reward_webhook(
    *,
    config: Dict[str, Any],
    agent_id: str,
    referral_code: str,
    event_type: str = "knowledge_capture",
    amount_cents: int = 100,
    test_mode: bool = False,
) -> Dict[str, Any]:
    """Fire an HMAC-signed webhook to seren-affiliates for a reward event.

    Args:
        config: Skill config dict (must contain affiliates_* keys)
        agent_id: User ID from authenticated session
        referral_code: User's affiliate referral code from session
        event_type: "knowledge_capture" or "knowledge_retrieval"
        amount_cents: Notional value in cents
        test_mode: If True, affiliates processes but does not pay out

    Returns:
        Dict with "status", "transaction_id", and optionally "error"
    """
    webhook_url = config.get("affiliates_webhook_url", "")
    webhook_secret = config.get("affiliates_webhook_secret", "")
    publisher_id = config.get("affiliates_publisher_id", "")

    if not webhook_url or not webhook_secret or not publisher_id:
        return {
            "status": "skipped",
            "reason": "affiliates_webhook_url, affiliates_webhook_secret, or affiliates_publisher_id not configured",
        }

    payload = build_reward_payload(
        publisher_id=publisher_id,
        publisher_slug=publisher_id,
        agent_id=agent_id,
        referral_code=referral_code,
        amount_cents=amount_cents,
        event_type=event_type,
        test_mode=test_mode,
    )

    payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    signature = _sign_payload(payload_bytes, webhook_secret)

    req = urllib.request.Request(
        webhook_url,
        data=payload_bytes,
        headers={
            "Content-Type": "application/json",
            "X-Webhook-Signature": signature,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp_body = resp.read().decode("utf-8", errors="replace")
            return {
                "status": "sent",
                "transaction_id": payload["transaction_id"],
                "http_status": resp.status,
                "response": resp_body[:500],
            }
    except Exception as e:
        return {
            "status": "failed",
            "transaction_id": payload["transaction_id"],
            "error": str(e),
        }
