from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

from common import utc_now

EPOCH_WATERMARK = "1970-01-01T00:00:00Z"
DEFAULT_HTTP_TIMEOUT_SECONDS = 10
MAX_PAGES_PER_RUN = 50  # 50 * 500 = 25k opt-outs per run, plenty per affiliate

SAMPLE_PROGRAMS = [
    {
        "program_slug": "sample-saas-alpha",
        "program_name": "SaaS Alpha",
        "program_description": "Lightweight analytics for small engineering teams.",
        "partner_link_url": "https://example.com/r/saas-alpha?ref=seren-demo",
        "commission_summary_json": {
            "commission_type": "percentage",
            "rate_bps": 2000,
            "tier": "bronze",
        },
        "joined_at": "2026-03-10T00:00:00Z",
    },
    {
        "program_slug": "sample-devtool-beta",
        "program_name": "Devtool Beta",
        "program_description": "CI telemetry and flaky test triage for Python shops.",
        "partner_link_url": "https://example.com/r/devtool-beta?ref=seren-demo",
        "commission_summary_json": {
            "commission_type": "fixed",
            "fixed_cents": 2500,
            "tier": "bronze",
        },
        "joined_at": "2026-03-27T00:00:00Z",
    },
]


def sync_joined_programs(config: dict) -> dict:
    simulate = config.get("simulate", {})
    if bool(simulate.get("affiliate_bootstrap_failure")):
        return {
            "status": "error",
            "error_code": "affiliate_bootstrap_failed",
            "retry_count": 3,
            "fail_closed": True,
            "message": "GET /affiliates/me/partner-links failed three consecutive attempts.",
        }

    now = utc_now()
    programs = []
    for entry in SAMPLE_PROGRAMS:
        programs.append({**entry, "last_synced_at": now})
    return {
        "status": "ok",
        "count": len(programs),
        "programs": programs,
        "last_synced_at": now,
    }


def _default_http_get(url: str, *, timeout: int = DEFAULT_HTTP_TIMEOUT_SECONDS) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def sync_remote_unsubscribes(
    *,
    config: dict,
    agent_id: str,
    watermark: str | None,
    resolve_token: Callable[[str], str | None],
    http_get: Callable[[str], dict] | None = None,
) -> dict:
    """Pull link_click opt-outs from seren-affiliates-website and resolve
    tokens to emails via the local distributions table.

    Stale-blocklist behavior: any HTTP failure sets stale=True, returns an
    ok status with pulled_count=0, and does NOT advance the watermark. The
    pipeline must continue so a website outage never blocks affiliate campaigns.

    resolve_token(token) returns an email (token known) or None (unknown
    token — stale distributions row or wiped DB). Unknown tokens are logged
    and skipped, never raised.
    """
    simulate = config.get("simulate", {})
    if bool(simulate.get("public_unsubscribes_api_down")):
        return {
            "status": "ok",
            "stale": True,
            "pulled_count": 0,
            "resolved_count": 0,
            "unresolved_count": 0,
            "new_unsubscribes": [],
            "next_watermark": watermark,
            "warning": "public_unsubscribes_api_unavailable",
            "note": (
                "Proceeding with stale blocklist per issue #421 design — a "
                "website outage must not block affiliate campaigns."
            ),
        }

    simulated_page = simulate.get("public_unsubscribes_response")
    if simulated_page is not None:
        get = lambda _url: simulated_page  # noqa: E731
    else:
        get = http_get or _default_http_get

    base = str(config["unsubscribe"]["sync_api_base"]).rstrip("?")
    since = watermark or EPOCH_WATERMARK
    cursor: str | None = None
    now = utc_now()

    pulled: list[dict] = []
    resolved: list[dict] = []
    unresolved_tokens: list[str] = []
    max_seen = since

    for _page_idx in range(MAX_PAGES_PER_RUN):
        params = {"agent_id": agent_id, "since": since}
        if cursor:
            params["cursor"] = cursor
        url = f"{base}?{urllib.parse.urlencode(params)}"
        try:
            payload = get(url)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
            return {
                "status": "ok",
                "stale": True,
                "pulled_count": len(pulled),
                "resolved_count": len(resolved),
                "unresolved_count": len(unresolved_tokens),
                "new_unsubscribes": resolved,
                "next_watermark": watermark,
                "warning": f"public_unsubscribes_api_unavailable: {type(exc).__name__}",
            }

        for row in payload.get("unsubscribes", []) or []:
            token = str(row.get("token", "")).strip()
            unsubbed_at = str(row.get("unsubscribed_at", "")).strip()
            if not token or not unsubbed_at:
                continue
            pulled.append({"token": token, "unsubscribed_at": unsubbed_at})
            email = resolve_token(token)
            if email is None:
                unresolved_tokens.append(token)
                continue
            resolved.append(
                {
                    "email": email,
                    "unsubscribed_at": unsubbed_at,
                    "source": "link_click",
                    "agent_id": agent_id,
                    "unsubscribe_token": token,
                }
            )
            if unsubbed_at > max_seen:
                max_seen = unsubbed_at

        cursor = payload.get("next_cursor")
        if not cursor:
            break

    return {
        "status": "ok",
        "stale": False,
        "pulled_count": len(pulled),
        "resolved_count": len(resolved),
        "unresolved_count": len(unresolved_tokens),
        "unresolved_tokens_sample": unresolved_tokens[:10],
        "new_unsubscribes": resolved,
        "next_watermark": now if max_seen == since else max_seen,
    }


def select_program(config: dict, programs: list[dict]) -> dict:
    requested = str(config["inputs"].get("program_slug", "")).strip()
    if not requested:
        return {
            "status": "needs_program_slug",
            "message": (
                "program_slug is required. Pick one of the joined programs "
                "or re-run `bootstrap` to refresh the list."
            ),
            "available": [p["program_slug"] for p in programs],
        }
    for program in programs:
        if program["program_slug"] == requested:
            return {"status": "ok", "program": program}
    return {
        "status": "error",
        "error_code": "unknown_program_slug",
        "message": f"program_slug '{requested}' is not in the joined programs cache.",
        "available": [p["program_slug"] for p in programs],
    }
