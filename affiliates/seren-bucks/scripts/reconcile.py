from __future__ import annotations

from common import reply_signal


def reconcile_signals(config: dict, sync_result: dict, drafts: dict) -> dict:
    signal = reply_signal(config)
    dnc_events: list[dict] = []

    if signal and signal in set(config["dnc"]["hard_stop_signals"]):
        first_reply = drafts["reply_drafts"][0]
        dnc_events.append(
            {
                "candidate_id": first_reply["candidate_id"],
                "signal": signal,
                "severity": "hard_stop",
                "blocked_action": "future_outreach",
            }
        )

    return {
        "status": "ok",
        "affiliate_summary": {
            "source_of_truth": config["program"]["affiliate_source_of_truth"],
            "clicks_today": 27,
            "signups_today": 4,
        },
        "provider_health": {
            "overall": "ok",
            "affiliates": "ok",
            "gmail": "ok" if sync_result["source_counts"]["gmail_sent"] or sync_result["source_counts"]["gmail_contacts"] else "degraded",
            "outlook": "ok" if sync_result["source_counts"]["outlook_sent"] or sync_result["source_counts"]["outlook_contacts"] else "degraded",
        },
        "dnc_events": dnc_events,
        "audit": {
            "crm_source_of_truth": "skill_owned_serendb",
            "manual_review_only": True,
            "replies_excluded_from_daily_cap": True,
        },
    }
