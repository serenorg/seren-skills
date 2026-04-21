from __future__ import annotations

from email_filter import is_personal_relationship, compute_personal_score_penalty


SOURCE_CATALOG = {
    "gmail_sent": [
        {
            "candidate_id": "cand-amelia-ross",
            "full_name": "Amelia Ross",
            "email": "amelia@studioalpha.com",
            "organization": "Studio Alpha",
            "source_system": "gmail_sent",
            "relationship_hint": "warm",
            "candidate_score": 93,
            "dnc_status": "active",
        },
        {
            "candidate_id": "cand-daniel-kim",
            "full_name": "Daniel Kim",
            "email": "daniel@northdock.com",
            "organization": "North Dock",
            "source_system": "gmail_sent",
            "relationship_hint": "warm",
            "candidate_score": 88,
            "dnc_status": "active",
        },
    ],
    "outlook_sent": [
        {
            "candidate_id": "cand-zoe-patel",
            "full_name": "Zoe Patel",
            "email": "zoe@relayops.io",
            "organization": "RelayOps",
            "source_system": "outlook_sent",
            "relationship_hint": "warm",
            "candidate_score": 90,
            "dnc_status": "active",
        }
    ],
    "gmail_contacts": [
        {
            "candidate_id": "cand-mika-jones",
            "full_name": "Mika Jones",
            "email": "mika@signalfield.com",
            "organization": "SignalField",
            "source_system": "gmail_contacts",
            "relationship_hint": "cold",
            "candidate_score": 81,
            "dnc_status": "active",
        }
    ],
    "outlook_contacts": [
        {
            "candidate_id": "cand-ivy-cole",
            "full_name": "Ivy Cole",
            "email": "ivy@meridianhq.com",
            "organization": "Meridian HQ",
            "source_system": "outlook_contacts",
            "relationship_hint": "cold",
            "candidate_score": 79,
            "dnc_status": "active",
        }
    ],
}


def sync_candidates(config: dict) -> dict:
    source_flags = config["candidate_sources"]
    source_counts: dict[str, int] = {}
    candidates_by_id: dict[str, dict] = {}
    filtered_out_business: int = 0

    for source_name, enabled in source_flags.items():
        if not enabled:
            source_counts[source_name] = 0
            continue

        source_items = SOURCE_CATALOG.get(source_name, [])
        source_counts[source_name] = len(source_items)
        for item in source_items:
            email = item.get("email", "")
            context = {"thread_content": item.get("thread_content", "")}

            if not is_personal_relationship(email, context):
                filtered_out_business += 1
                continue

            penalty = compute_personal_score_penalty(email, context)
            adjusted_score = max(0, item["candidate_score"] - penalty)
            item_copy = dict(item)
            item_copy["candidate_score"] = adjusted_score
            item_copy["personal_filter_applied"] = True
            candidates_by_id[item["candidate_id"]] = item_copy

    candidates = sorted(
        candidates_by_id.values(),
        key=lambda item: item["candidate_score"],
        reverse=True,
    )

    return {
        "status": "ok",
        "persist_immediately": True,
        "crm_source_of_truth": "skill_owned_serendb",
        "source_counts": source_counts,
        "discovered_count": len(candidates),
        "filtered_out_business_emails": filtered_out_business,
        "candidates": candidates,
    }
