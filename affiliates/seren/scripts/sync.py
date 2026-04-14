from __future__ import annotations

from common import utc_now

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
