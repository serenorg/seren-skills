from __future__ import annotations

from common import select_auth_path, tracked_link


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
    }


def bootstrap_affiliate_context(config: dict) -> dict:
    failure = bool(config.get("simulate", {}).get("affiliate_bootstrap_failure", False))
    if failure:
        return {
            "status": "error",
            "error_code": "affiliate_bootstrap_failed",
            "affiliate_feed_status": "unavailable",
            "retry_count": 3,
            "fail_closed": True,
            "message": "Default affiliate campaign context failed three immediate bootstrap attempts.",
        }

    campaign = {
        "campaign_id": config["campaign"]["campaign_id"],
        "campaign_name": config["campaign"]["campaign_name"],
        "tracked_link": tracked_link(config),
        "source_of_truth": config["campaign"]["affiliate_source_of_truth"],
    }
    return {
        "status": "ok",
        "retry_count": 1,
        "affiliate_feed_status": "ready",
        "campaign": campaign,
    }
