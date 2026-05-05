from __future__ import annotations

from common import select_auth_path, utc_now


def bootstrap_auth_and_db(config: dict) -> dict:
    auth_path = select_auth_path(config)
    if auth_path == "setup_required":
        return {
            "status": "error",
            "error_code": "auth_setup_required",
            "message": "No Seren Desktop auth or SEREN_API_KEY was found.",
            "setup_url": config["auth"]["setup_url"],
        }

    return {
        "status": "ok",
        "auth_path": auth_path,
        "database_status": "ready",
        "database": config["database"],
        "started_at": utc_now(),
    }


def sync_affiliate_profile(config: dict) -> dict:
    simulate = config.get("simulate", {})
    if bool(simulate.get("affiliate_bootstrap_failure")):
        return {
            "status": "error",
            "error_code": "affiliate_bootstrap_failed",
            "retry_count": 3,
            "fail_closed": True,
            "message": "GET /affiliates/me failed three consecutive attempts.",
        }

    registered = False
    if bool(simulate.get("profile_missing")):
        registered = True

    sender_address = (
        "" if bool(simulate.get("sender_address_missing")) else "1 Market Street, San Francisco CA"
    )

    profile = {
        "agent_id": "agent-demo-0001",
        "referral_code": "seren-demo",
        "tier": "bronze",
        "balance_cents": 0,
        "display_name": "Seren Demo Affiliate",
        "sender_address": sender_address,
        "last_synced_at": utc_now(),
    }

    if not sender_address:
        return {
            "status": "error",
            "error_code": "no_sender_address",
            "message": (
                "affiliate_profile.sender_address is empty. "
                "Set it once before sending any distributions."
            ),
            "profile": profile,
        }

    return {
        "status": "ok",
        "registered_this_run": registered,
        "profile": profile,
        "source_of_truth": config["affiliate_source_of_truth"],
    }
