from __future__ import annotations

from email_filter import is_personal_relationship, compute_personal_score_penalty

PUBLISHER_ROUTING = {
    "gmail_sent": "gmail",
    "outlook_sent": "outlook",
    "gmail_contacts": "google-contacts",
    "outlook_contacts": "outlook-contacts",
}

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

            if not is_personal_relationship(email):
                filtered_out_business += 1
                continue

            penalty = compute_personal_score_penalty(email)
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

    target = int(config["inputs"].get("proposal_size", config["limits"]["proposal_size"]))
    target = max(1, min(target, 10))
    sources_exhausted = [
        source_name
        for source_name, enabled in source_flags.items()
        if enabled
    ]
    quota_shortfall = len(candidates) < target

    return {
        "status": "ok",
        "persist_immediately": True,
        "crm_source_of_truth": "skill_owned_serendb",
        "source_counts": source_counts,
        "discovered_count": len(candidates),
        "qualified_count": len(candidates),
        "target": target,
        "quota_shortfall": quota_shortfall,
        "sources_exhausted": sources_exhausted if quota_shortfall else [],
        "filtered_out_business_emails": filtered_out_business,
        "candidates": candidates,
    }
