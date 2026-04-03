from __future__ import annotations

from common import daily_cap, tracked_link


def build_daily_digest(
    *,
    config: dict,
    auth_db: dict,
    affiliate: dict,
    sync_result: dict,
    proposal: dict,
    drafts: dict,
    send_plan: dict,
    reconciliation: dict,
) -> dict:
    cap = daily_cap(config)
    used = len(drafts["new_outbound"])
    remaining = max(cap - used, 0)

    proposal_lines = [
        f"{item['rank_position']}. {item['full_name']} ({item['organization']})"
        for item in proposal["top10"]
    ]
    digest_markdown = "\n".join(
        [
            "# Seren Bucks",
            "",
            "## Campaign",
            f"- Campaign: {affiliate['campaign']['campaign_name']}",
            f"- Tracked link: {tracked_link(config)}",
            f"- Affiliate feed health: {affiliate['affiliate_feed_status']}",
            f"- Auth path: {auth_db['auth_path']}",
            "",
            "## Candidate Sync",
            f"- Discovered candidates: {sync_result['discovered_count']}",
            f"- Source counts: {sync_result['source_counts']}",
            "",
            "## Proposal Top 10",
            *[f"- {line}" for line in proposal_lines],
            "",
            "## Approvals",
            f"- New outbound pending: {send_plan['new_outbound_batch']['count']}",
            f"- Replies pending: {send_plan['reply_batch']['count']}",
            "",
            "## DNC",
            f"- New hard-stop events: {len(reconciliation['dnc_events'])}",
            "",
            "## Daily Cap",
            f"- New outbound used: {used}/{cap}",
            f"- New outbound remaining: {remaining}",
            "- Replies excluded from cap: true",
        ]
    )

    return {
        "status": "ok",
        "markdown": digest_markdown,
        "daily_cap": {
            "new_outbound_used": used,
            "new_outbound_remaining": remaining,
            "new_outbound_daily_cap": cap,
            "replies_count_against_daily_cap": False,
        },
    }
