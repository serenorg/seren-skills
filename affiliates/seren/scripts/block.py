from __future__ import annotations

from common import is_valid_email, utc_now


def block_email(config: dict) -> dict:
    raw = str(config["inputs"].get("block_email", "")).strip().lower()
    if not raw:
        return {
            "status": "error",
            "error_code": "missing_block_email",
            "message": "block command requires block_email input.",
        }
    if not is_valid_email(raw):
        return {
            "status": "error",
            "error_code": "invalid_email",
            "message": f"block_email '{raw}' is not a valid email address.",
        }
    return {
        "status": "ok",
        "unsubscribe": {
            "email": raw,
            "unsubscribed_at": utc_now(),
            "source": "operator_manual",
        },
        "phase": "phase1_operator_blocklist_only",
    }
