from __future__ import annotations

from common import utc_now


def fetch_live_stats(*, config: dict, program_slug: str) -> dict:
    return {
        "status": "ok",
        "program_slug": program_slug,
        "fetched_at": utc_now(),
        "stats": {
            "clicks_today": 12,
            "clicks_lifetime": 143,
            "conversions_today": 1,
            "conversions_lifetime": 9,
        },
        "commissions": {
            "pending_cents": 500,
            "paid_cents": 7500,
            "currency": "USD",
        },
        "source_of_truth": config["affiliate_source_of_truth"],
    }


def render_report(
    *,
    config: dict,
    run_id: str,
    command: str,
    program: dict | None,
    profile: dict,
    provider_used: str | None,
    ingest_summary: dict,
    eligibility: dict,
    cap_summary: dict,
    draft: dict | None,
    approval: dict | None,
    send_result: dict | None,
    live: dict | None,
) -> dict:
    json_output = bool(config["inputs"].get("json_output"))
    local = {
        "skill": "affiliates",
        "run_id": run_id,
        "command": command,
        "generated_at": utc_now(),
        "program": program,
        "provider_used": provider_used,
        "profile": {
            "agent_id": profile.get("agent_id"),
            "referral_code": profile.get("referral_code"),
            "tier": profile.get("tier"),
            "sender_address_present": bool(profile.get("sender_address")),
        },
        "contacts": {
            "ingested": ingest_summary.get("count", 0),
            "eligible": eligibility.get("eligible_count", 0),
            "skipped_dedupe": eligibility.get("skipped_dedupe", 0),
            "skipped_unsub": eligibility.get("skipped_unsub", 0),
            "cap": cap_summary.get("cap", 0),
            "remaining_before_run": cap_summary.get("remaining_before_run", 0),
            "clipped_by_cap": cap_summary.get("clipped_count", 0),
        },
        "draft": {
            "subject": draft["subject"] if draft else None,
            "model_used": draft.get("model_used") if draft else None,
            "approval_status": approval["status"] if approval else None,
        },
        "send": {
            "sent_count": send_result.get("sent_count", 0) if send_result else 0,
            "new_unsubscribes": len(send_result["new_unsubscribes"]) if send_result else 0,
        },
        "live": live,
        "unsubscribe_live": True,
    }
    if json_output:
        return local
    return {
        "json": local,
        "human": _format_human(local),
    }


def _format_human(local: dict) -> str:
    lines = [
        f"affiliates {local['command']} — {local['run_id']}",
        f"generated_at: {local['generated_at']}",
    ]
    program = local.get("program") or {}
    if program:
        lines.append(f"program: {program.get('program_slug')} ({program.get('program_name')})")
    lines.append(f"provider_used: {local.get('provider_used')}")
    c = local["contacts"]
    lines.append(
        f"contacts: ingested={c['ingested']} eligible={c['eligible']} "
        f"skipped_dedupe={c['skipped_dedupe']} skipped_unsub={c['skipped_unsub']} "
        f"cap={c['cap']} remaining={c['remaining_before_run']} clipped={c['clipped_by_cap']}"
    )
    d = local["draft"]
    lines.append(f"draft: {d['subject']} [{d['approval_status']}] model={d['model_used']}")
    s = local["send"]
    lines.append(
        f"send: sent_count={s['sent_count']} new_unsubscribes={s['new_unsubscribes']}"
    )
    live = local.get("live")
    if live:
        stats = live.get("stats", {})
        commissions = live.get("commissions", {})
        lines.append(
            f"live: clicks_today={stats.get('clicks_today')} "
            f"conversions_today={stats.get('conversions_today')} "
            f"pending_cents={commissions.get('pending_cents')} "
            f"paid_cents={commissions.get('paid_cents')}"
        )
    return "\n".join(lines)
