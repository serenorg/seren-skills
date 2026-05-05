from __future__ import annotations


def prepare_send_actions(drafts: dict, config: dict) -> dict:
    return {
        "status": "ok",
        "manual_review_required": True,
        "new_outbound_batch": {
            "status": "pending_approval",
            "approval_required": config["approval"]["new_outbound_requires_approval"],
            "count": len(drafts["new_outbound"]),
        },
        "reply_batch": {
            "status": "pending_approval",
            "approval_required": config["approval"]["replies_require_approval"],
            "count": len(drafts["reply_drafts"]),
        },
    }
