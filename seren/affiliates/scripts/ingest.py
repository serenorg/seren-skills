from __future__ import annotations

from common import is_valid_email, parse_pasted_contacts, utc_now

SAMPLE_GMAIL_CONTACTS = [
    {"email": "alice@example.com", "display_name": "Alice Chen"},
    {"email": "bob@example.com", "display_name": "Bob Weaver"},
    {"email": "carol@example.org", "display_name": "Carol Diaz"},
]

SAMPLE_OUTLOOK_CONTACTS = [
    {"email": "dave@example.com", "display_name": "Dave Okafor"},
    {"email": "eva@example.net", "display_name": "Eva Lindqvist"},
]


def _tagged(records: list[dict], source: str) -> list[dict]:
    now = utc_now()
    out = []
    for record in records:
        email = str(record.get("email", "")).strip().lower()
        if not is_valid_email(email):
            continue
        out.append(
            {
                "email": email,
                "display_name": str(record.get("display_name", "")).strip(),
                "source_kind": source,
                "first_seen_at": now,
                "last_updated_at": now,
            }
        )
    return out


def ingest_contacts(config: dict) -> dict:
    source = str(config["inputs"].get("contacts_source", "pasted"))
    allowed = set(config["contacts"]["allowed_sources"])
    if source not in allowed:
        return {
            "status": "error",
            "error_code": "invalid_contacts_source",
            "message": f"contacts_source '{source}' is not one of {sorted(allowed)}.",
            "contacts": [],
        }

    if source == "pasted":
        raw = str(config["inputs"].get("contacts", ""))
        parsed = parse_pasted_contacts(raw)
        return {
            "status": "ok",
            "source_kind": "pasted",
            "count": len(parsed),
            "contacts": _tagged(parsed, "pasted"),
        }

    if source == "gmail_contacts":
        return {
            "status": "ok",
            "source_kind": "gmail_contacts",
            "count": len(SAMPLE_GMAIL_CONTACTS),
            "contacts": _tagged(SAMPLE_GMAIL_CONTACTS, "gmail_contacts"),
        }

    return {
        "status": "ok",
        "source_kind": "outlook_contacts",
        "count": len(SAMPLE_OUTLOOK_CONTACTS),
        "contacts": _tagged(SAMPLE_OUTLOOK_CONTACTS, "outlook_contacts"),
    }


def resolve_provider(config: dict) -> dict:
    requested = str(config["inputs"].get("provider", "auto"))
    simulate = config.get("simulate", {})

    if bool(simulate.get("no_provider_authorized")):
        return {
            "status": "error",
            "error_code": "no_provider_authorized",
            "message": (
                "Neither gmail nor microsoft-outlook publishers are authorized "
                "for this caller. Authorize at the Seren platform level."
            ),
        }

    preferred_order = config["providers"]["preferred_order"]
    if requested == "auto":
        chosen = preferred_order[0]
    else:
        chosen = requested

    if chosen not in {"gmail", "outlook"}:
        return {
            "status": "error",
            "error_code": "invalid_provider",
            "message": f"provider '{chosen}' must be gmail, outlook, or auto.",
        }

    return {
        "status": "ok",
        "provider_used": chosen,
        "resolution_mode": "auto" if requested == "auto" else "explicit",
    }


def filter_eligible(
    *,
    contacts: list[dict],
    program_slug: str,
    already_sent_for_program: set[str],
    unsubscribes: set[str],
) -> dict:
    eligible: list[dict] = []
    skipped_dedupe = 0
    skipped_unsub = 0
    for contact in contacts:
        email = contact["email"]
        if email in unsubscribes:
            skipped_unsub += 1
            continue
        if email in already_sent_for_program:
            skipped_dedupe += 1
            continue
        eligible.append(contact)
    return {
        "status": "ok",
        "program_slug": program_slug,
        "eligible_count": len(eligible),
        "eligible": eligible,
        "skipped_dedupe": skipped_dedupe,
        "skipped_unsub": skipped_unsub,
    }


def enforce_daily_cap(
    *,
    eligible: list[dict],
    cap: int,
    already_sent_today: int,
) -> dict:
    remaining = max(0, cap - already_sent_today)
    clipped = eligible[:remaining]
    return {
        "status": "ok",
        "cap": cap,
        "already_sent_today": already_sent_today,
        "remaining_before_run": remaining,
        "sendable": clipped,
        "clipped_count": len(eligible) - len(clipped),
    }
