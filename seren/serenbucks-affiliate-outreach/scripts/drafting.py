from __future__ import annotations

from common import daily_cap, tracked_link

SAMPLE_REPLY_QUEUE = [
    {
        "candidate_id": "cand-amelia-ross",
        "full_name": "Amelia Ross",
        "organization": "Studio Alpha",
        "reply_summary": "Asked for a short explanation of the SerenBucks program.",
    },
    {
        "candidate_id": "cand-zoe-patel",
        "full_name": "Zoe Patel",
        "organization": "RelayOps",
        "reply_summary": "Interested but asked about payout cadence.",
    },
]


def build_draft_batches(proposal: dict, config: dict) -> dict:
    cap = daily_cap(config)
    link = tracked_link(config)

    new_outbound = []
    for candidate in proposal["top10"][:cap]:
        new_outbound.append(
            {
                "draft_id": f"draft-{candidate['candidate_id']}",
                "draft_type": "new_outbound",
                "candidate_id": candidate["candidate_id"],
                "subject_line": f"{candidate['organization']} x SerenBucks affiliate fit",
                "message_body": (
                    f"Draft outreach for {candidate['full_name']}.\n\n"
                    f"Include the default tracked link: {link}"
                ),
                "tracked_link": link,
                "approval_required": True,
            }
        )

    reply_drafts = []
    for reply in SAMPLE_REPLY_QUEUE:
        reply_drafts.append(
            {
                "draft_id": f"reply-{reply['candidate_id']}",
                "draft_type": "reply",
                "candidate_id": reply["candidate_id"],
                "message_body": (
                    f"Draft reply for {reply['full_name']}.\n\n"
                    f"Address: {reply['reply_summary']}"
                ),
                "approval_required": True,
            }
        )

    return {
        "status": "ok",
        "manual_review_required": True,
        "new_outbound": new_outbound,
        "reply_drafts": reply_drafts,
        "daily_cap": cap,
        "replies_count_against_daily_cap": False,
    }
