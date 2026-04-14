from __future__ import annotations

from common import hash_body, unsubscribe_link, utc_now


def _merge(*, body_template: str, contact: dict, profile: dict, partner_link: str, link: str) -> str:
    display = contact.get("display_name") or contact["email"].split("@", 1)[0]
    sender_identity = profile.get("display_name") or profile["agent_id"]
    sender_address = profile.get("sender_address") or ""
    return (
        body_template.replace("{name}", display)
        .replace("{partner_link}", partner_link)
        .replace("{sender_identity}", sender_identity)
        .replace("{sender_address}", sender_address)
        .replace("{unsubscribe_link}", link)
    )


def merge_and_send(
    *,
    config: dict,
    run_id: str,
    profile: dict,
    program: dict,
    provider_used: str,
    draft: dict,
    sendable: list[dict],
    approval: dict,
) -> dict:
    if approval["status"] != "approved":
        return {
            "status": "blocked",
            "error_code": "awaiting_approval",
            "message": "merge_and_send is blocked until approval is recorded.",
            "sent": [],
            "new_unsubscribes": [],
        }

    hard_bounce_email = str(config.get("simulate", {}).get("hard_bounce_email", "")).strip().lower()
    sent: list[dict] = []
    new_unsubscribes: list[dict] = []
    now = utc_now()

    for contact in sendable:
        token_link = unsubscribe_link(
            config=config,
            email=contact["email"],
            program_slug=program["program_slug"],
            run_id=run_id,
        )
        merged_body = _merge(
            body_template=draft["body_template"],
            contact=contact,
            profile=profile,
            partner_link=program["partner_link_url"],
            link=token_link,
        )
        if contact["email"] == hard_bounce_email:
            new_unsubscribes.append(
                {
                    "email": contact["email"],
                    "unsubscribed_at": now,
                    "source": "hard_bounce",
                }
            )
            continue

        token_suffix = token_link.rsplit("/", 1)[-1]
        sent.append(
            {
                "run_id": run_id,
                "program_slug": program["program_slug"],
                "contact_email": contact["email"],
                "provider": provider_used,
                "subject_final": draft["subject"],
                "body_hash": hash_body(merged_body),
                "provider_message_id": f"{provider_used}-msg-{contact['email']}",
                "unsubscribe_token": token_suffix,
                "sent_at": now,
            }
        )

    return {
        "status": "ok",
        "sent_count": len(sent),
        "sent": sent,
        "new_unsubscribes": new_unsubscribes,
        "provider_used": provider_used,
        "audit": {
            "authoritative_success_signal": "provider_message_id",
            "no_delivery_polling": True,
        },
    }
