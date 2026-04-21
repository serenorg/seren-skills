from __future__ import annotations

from common import proposal_size
from email_filter import is_personal_relationship


def build_editable_top10(candidates: list[dict], config: dict) -> dict:
    ranked = [
        candidate
        for candidate in candidates
        if candidate["dnc_status"] != "blocked"
        and is_personal_relationship(candidate.get("email", ""))
    ]
    ranked = sorted(ranked, key=lambda item: item["candidate_score"], reverse=True)
    limit = proposal_size(config)

    top_candidates = []
    for index, candidate in enumerate(ranked[:limit], start=1):
        top_candidates.append(
            {
                "rank_position": index,
                "candidate_id": candidate["candidate_id"],
                "full_name": candidate["full_name"],
                "email": candidate.get("email", ""),
                "organization": candidate["organization"],
                "candidate_score": candidate["candidate_score"],
                "personal_filter_applied": candidate.get("personal_filter_applied", False),
                "editable": True,
            }
        )

    return {
        "status": "ok",
        "editable": True,
        "proposal_size": limit,
        "personal_only": True,
        "top10": top_candidates,
    }
