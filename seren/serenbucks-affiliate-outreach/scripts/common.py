from __future__ import annotations

import json
import os
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_CONFIG: dict[str, Any] = {
    "dry_run": True,
    "skill": "serenbucks-affiliate-outreach",
    "campaign": {
        "campaign_id": "serenbucks-default",
        "campaign_name": "SerenBucks Default Affiliate Campaign",
        "tracked_link": "https://seren.ai/serenbucks?ref=default",
        "affiliate_source_of_truth": "seren-affiliates",
    },
    "database": {
        "project": "seren-affiliate-outreach",
        "name": "serenbucks_affiliate_outreach",
    },
    "auth": {
        "desktop_auth_first": True,
        "api_key_env": "SEREN_API_KEY",
        "setup_url": "https://docs.serendb.com/skills.md",
    },
    "candidate_sources": {
        "gmail_sent": True,
        "outlook_sent": True,
        "gmail_contacts": True,
        "outlook_contacts": True,
    },
    "approval": {
        "new_outbound_requires_approval": True,
        "replies_require_approval": True,
    },
    "limits": {
        "proposal_size": 10,
        "new_outbound_daily_cap": 10,
        "replies_count_against_daily_cap": False,
    },
    "dnc": {
        "hard_stop_signals": [
            "unsubscribe",
            "do_not_contact",
            "hostile_negative",
        ]
    },
    "inputs": {
        "command": "run",
        "json_output": False,
        "proposal_size": 10,
        "new_outbound_daily_cap": 10,
        "strict_mode": True,
        "tracked_link": "https://seren.ai/serenbucks?ref=default",
    },
    "simulate": {
        "affiliate_bootstrap_failure": False,
        "reply_signal": "",
    },
}


def utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z")


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if (
            isinstance(value, dict)
            and isinstance(merged.get(key), dict)
        ):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(config_path: str) -> dict[str, Any]:
    path = Path(config_path)
    if not path.exists():
        return deepcopy(DEFAULT_CONFIG)
    return deep_merge(DEFAULT_CONFIG, json.loads(path.read_text(encoding="utf-8")))


def tracked_link(config: dict[str, Any]) -> str:
    return str(config["inputs"].get("tracked_link") or config["campaign"]["tracked_link"])


def proposal_size(config: dict[str, Any]) -> int:
    value = int(config["inputs"].get("proposal_size", config["limits"]["proposal_size"]))
    return max(1, min(value, 10))


def daily_cap(config: dict[str, Any]) -> int:
    value = int(
        config["inputs"].get(
            "new_outbound_daily_cap",
            config["limits"]["new_outbound_daily_cap"],
        )
    )
    return max(1, min(value, 10))


def select_auth_path(config: dict[str, Any]) -> str:
    desktop_auth_first = bool(config["auth"].get("desktop_auth_first", True))
    if desktop_auth_first and os.environ.get("API_KEY"):
        return "seren_desktop"
    if os.environ.get(config["auth"].get("api_key_env", "SEREN_API_KEY")):
        return "seren_api_key"
    return "setup_required"


def reply_signal(config: dict[str, Any]) -> str:
    return str(config.get("simulate", {}).get("reply_signal", "")).strip().lower()
