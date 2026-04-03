from __future__ import annotations

from common import proposal_size


def build_editable_top10(candidates: list[dict], config: dict) -> dict:
    ranked = [candidate for candidate in candidates if candidate["dnc_status"] != "blocked"]
    ranked = sorted(ranked, key=lambda item: item["candidate_score"], reverse=True)
    limit = proposal_size(config)

    top_candidates = []
    for index, candidate in enumerate(ranked[:limit], start=1):
        top_candidates.append(
            {
                "rank_position": index,
                "candidate_id": candidate["candidate_id"],
                "full_name": candidate["full_name"],
                "organization": candidate["organization"],
                "candidate_score": candidate["candidate_score"],
                "editable": True,
            }
        )

    return {
        "status": "ok",
        "editable": True,
        "proposal_size": limit,
        "top10": top_candidates,
    }
