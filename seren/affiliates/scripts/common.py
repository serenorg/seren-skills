from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_CONFIG: dict[str, Any] = {
    "dry_run": True,
    "skill": "affiliates",
    "database": {
        "project": "affiliates",
        "name": "seren_affiliate",
    },
    "auth": {
        "desktop_auth_first": True,
        "api_key_env": "SEREN_API_KEY",
        "setup_url": "https://docs.serendb.com/skills.md",
        "referral_token_secret_env": "REFERRAL_TOKEN_SECRET",
    },
    "affiliate_source_of_truth": "seren-affiliates",
    "providers": {
        "preferred_order": ["gmail", "outlook"],
    },
    "contacts": {
        "allowed_sources": ["pasted", "gmail_contacts", "outlook_contacts"],
    },
    "limits": {
        "daily_cap_default": 10,
        "daily_cap_max": 25,
    },
    "unsubscribe": {
        "endpoint_base": "https://affiliates-ui.serendb.com/unsubscribe",
        "sync_api_base": "https://affiliates-ui.serendb.com/public/unsubscribes",
        "unsubscribe_live": True,
    },
    "inputs": {
        "command": "run",
        "program_slug": "",
        "provider": "auto",
        "contacts_source": "pasted",
        "contacts": "",
        "voice_notes": "",
        "approve_draft": False,
        "daily_cap": 10,
        "json_output": False,
        "strict_mode": True,
        "block_email": "",
    },
    "simulate": {
        "affiliate_bootstrap_failure": False,
        "profile_missing": False,
        "sender_address_missing": False,
        "no_provider_authorized": False,
        "hard_bounce_email": "",
    },
}

REQUIRED_PLACEHOLDERS = (
    "{name}",
    "{partner_link}",
    "{sender_identity}",
    "{sender_address}",
    "{unsubscribe_link}",
)

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z")


def new_run_id(prefix: str = "run") -> str:
    return f"{prefix}-{uuid.uuid4()}"


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(config_path: str) -> dict[str, Any]:
    path = Path(config_path)
    if not path.exists():
        return deepcopy(DEFAULT_CONFIG)
    overlay = json.loads(path.read_text(encoding="utf-8"))
    return deep_merge(DEFAULT_CONFIG, overlay)


def select_auth_path(config: dict[str, Any]) -> str:
    desktop_first = bool(config["auth"].get("desktop_auth_first", True))
    if desktop_first and os.environ.get("API_KEY"):
        return "seren_desktop"
    if os.environ.get(config["auth"].get("api_key_env", "SEREN_API_KEY")):
        return "seren_api_key"
    return "setup_required"


def daily_cap_from_input(config: dict[str, Any]) -> int:
    raw = int(config["inputs"].get("daily_cap", config["limits"]["daily_cap_default"]))
    hard_max = int(config["limits"]["daily_cap_max"])
    return max(1, min(raw, hard_max))


def is_valid_email(value: str) -> bool:
    return bool(EMAIL_RE.match(value or ""))


def hash_body(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def unsubscribe_token(
    *,
    email: str,
    program_slug: str,
    run_id: str,
    secret: str | None,
) -> str:
    material = f"{email}|{program_slug}|{run_id}".encode("utf-8")
    key = (secret or "development-only-token").encode("utf-8")
    return hmac.new(key, material, hashlib.sha256).hexdigest()


def unsubscribe_link(
    *,
    config: dict[str, Any],
    email: str,
    program_slug: str,
    run_id: str,
    agent_id: str,
) -> str:
    secret_env = config["auth"].get("referral_token_secret_env", "REFERRAL_TOKEN_SECRET")
    token = unsubscribe_token(
        email=email,
        program_slug=program_slug,
        run_id=run_id,
        secret=os.environ.get(secret_env),
    )
    base = str(config["unsubscribe"]["endpoint_base"]).rstrip("/")
    return f"{base}/{agent_id}/{token}"


def footer_missing_placeholders(body_template: str) -> list[str]:
    return [token for token in REQUIRED_PLACEHOLDERS if token not in body_template]


def require_approve_draft_json_pairing(config: dict[str, Any]) -> dict[str, Any] | None:
    inputs = config["inputs"]
    if bool(inputs.get("approve_draft")) and not bool(inputs.get("json_output")):
        return {
            "status": "error",
            "error_code": "approve_draft_without_json_output",
            "message": (
                "approve_draft=true requires json_output=true so an unattended "
                "human CLI cannot accidentally skip the approval gate."
            ),
        }
    return None


def parse_pasted_contacts(raw: str) -> list[dict[str, str]]:
    contacts: list[dict[str, str]] = []
    if not raw:
        return contacts
    text = raw.strip()
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = []
        for item in parsed:
            if isinstance(item, dict) and is_valid_email(str(item.get("email", ""))):
                contacts.append(
                    {
                        "email": str(item["email"]).strip().lower(),
                        "display_name": str(item.get("name", "")).strip(),
                    }
                )
        return contacts
    for raw_line in re.split(r"[\n,]+", text):
        line = raw_line.strip()
        if not line:
            continue
        if "<" in line and ">" in line:
            name_part, email_part = line.rsplit("<", 1)
            email = email_part.split(">", 1)[0].strip().lower()
            name = name_part.strip().strip('"')
        else:
            email = line.strip().lower()
            name = ""
        if is_valid_email(email):
            contacts.append({"email": email, "display_name": name})
    return contacts
