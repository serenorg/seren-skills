"""BountyClient — thin wrapper around the seren-bounty publisher.

Plan §13.1. Three operations only:

  - join(bounty_id) → referral_code           POST /bounties/{id}/join
                                              natively idempotent on
                                              (bounty_id, user_id);
                                              re-calls return the same
                                              deterministic 12-char code.
  - submit(bounty_id, content_text)           POST /bounties/{id}/submission
                                              one submission per
                                              participant; subsequent
                                              calls REPLACE content.
  - earnings(bounty_id?) → list[dict]         GET /users/me/earnings

The skill never embeds the seren-bounty base URL or SEREN_API_KEY
directly — both are handled by the gateway. The client only shapes
publisher/path/body.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import BountyUnauthorized

PUBLISHER = "seren-bounty"


@dataclass
class JoinResult:
    bounty_id: str
    referral_code: str
    user_id: str = ""


class BountyClient:
    def __init__(self, *, gateway: Any) -> None:
        self.gateway = gateway

    def join(self, bounty_id: str) -> JoinResult:
        """POST /bounties/{id}/join. Idempotent — re-joining returns the same code."""
        response = self._call(
            "POST", f"/bounties/{bounty_id}/join", body=None
        )
        referral_code = response.get("referral_code") or ""
        if not referral_code:
            raise BountyUnauthorized(
                f"join({bounty_id}) returned no referral_code; auth or bounty state failure"
            )
        return JoinResult(
            bounty_id=response.get("bounty_id") or bounty_id,
            referral_code=referral_code,
            user_id=response.get("user_id") or "",
        )

    def submit(self, bounty_id: str, content_text: str) -> dict[str, Any]:
        """POST /bounties/{id}/submission. Subsequent calls REPLACE content.

        Plan §13.2: caller is responsible for assembling cumulative
        content_text including all prior runs' markets — the reconciler
        does that. Don't fold history here.
        """
        return self._call(
            "POST",
            f"/bounties/{bounty_id}/submission",
            body={"content_text": content_text, "content_prosemirror": None},
        )

    def list_my_bounties(
        self, *, customer_slug: str | None = None, status: str | None = None
    ) -> list[dict[str, Any]]:
        """GET /organizations/me/bounties for the authenticated org.

        Plan §3 ADR ("Bounty auto-resolution hardening"): the auto-resolve
        path filters by `customer_slug=prophet&status=open`, validates each
        candidate against `expected_bounty_spec.py`, and picks the newest
        match. This thin wrapper just shapes the query and unwraps the
        publisher response.
        """
        query = []
        if customer_slug:
            query.append(f"customer_slug={customer_slug}")
        if status:
            query.append(f"status={status}")
        path = "/organizations/me/bounties"
        if query:
            path = f"{path}?{'&'.join(query)}"
        response = self._call("GET", path, body=None)
        bounties = response.get("bounties")
        if not isinstance(bounties, list):
            return []
        return [b for b in bounties if isinstance(b, dict)]

    def earnings(self, *, bounty_id: str | None = None) -> list[dict[str, Any]]:
        """GET /users/me/earnings, optionally filtered by bounty_id.

        Used by the `status` command and the post-run summary. Returns
        the full earnings list; total/zero accounting is the caller's job.
        """
        path = "/users/me/earnings"
        headers: dict[str, str] = {}
        if bounty_id:
            headers["X-Query-Bounty-Id"] = bounty_id
        response = self._call("GET", path, body=None, headers=headers or None)
        earnings = response.get("earnings")
        if not isinstance(earnings, list):
            return []
        return earnings

    def _call(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        try:
            response = self.gateway.call(
                PUBLISHER, method, path, body=body, headers=headers or {}
            )
        except Exception:
            raise

        if isinstance(response, dict) and response.get("status") == 401:
            raise BountyUnauthorized(f"seren-bounty returned 401 on {method} {path}")
        if not isinstance(response, dict):
            return {}
        return response
