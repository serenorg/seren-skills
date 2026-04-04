"""Team memory system for family-office knowledge skill.

Structured memory objects, proactive resurfacing, stale-memory validation,
decision recall, engagement tracking, and digest generation.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from uuid import uuid4

# ---------------------------------------------------------------------------
# Memory types
# ---------------------------------------------------------------------------

MEMORY_TYPES = {
    "decision",
    "assumption",
    "preference",
    "relationship",
    "process",
    "open_question",
    "commitment",
    "risk",
    "source_claim",
    "counterpoint",
}

VALID_LINK_TYPES = {"people", "entities", "teams", "documents", "meetings", "time_periods", "memories"}

# Default review cadences by type (days)
DEFAULT_REVIEW_CADENCE: dict[str, int] = {
    "decision": 90,
    "assumption": 30,
    "preference": 180,
    "relationship": 120,
    "process": 60,
    "open_question": 14,
    "commitment": 30,
    "risk": 14,
    "source_claim": 60,
    "counterpoint": 30,
}

STORAGE_COLLECTIONS_TEAM_MEMORY = {
    "memory_objects",
    "memory_links",
    "memory_validations",
    "memory_subscriptions",
    "memory_nudges",
    "engagement_events",
}


# ---------------------------------------------------------------------------
# Memory object construction
# ---------------------------------------------------------------------------


def build_memory_object(
    *,
    memory_type: str,
    key_claim: str,
    subject: str = "",
    owner_id: str = "",
    team_scope: str = "team",
    organization_name: str = "",
    department: str = "",
    confidence: str = "medium",
    source: str = "",
    source_id: str = "",
    entity_refs: list[str] | None = None,
    derived_from_ids: list[str] | None = None,
    current_date: str = "",
    review_cadence_days: int | None = None,
) -> dict[str, Any]:
    """Create a structured memory object."""
    mtype = memory_type if memory_type in MEMORY_TYPES else "source_claim"
    now = current_date or date.today().isoformat()
    cadence = review_cadence_days if review_cadence_days is not None else DEFAULT_REVIEW_CADENCE.get(mtype, 30)
    return {
        "id": f"mem-{mtype[:4]}-{str(uuid4())[:8]}",
        "memory_type": mtype,
        "key_claim": key_claim,
        "subject": subject,
        "owner_id": owner_id,
        "team_scope": team_scope,
        "organization_name": organization_name,
        "department": department,
        "confidence_score": confidence,
        "importance_score": "medium",
        "validity_status": "active",
        "source": source,
        "source_id": source_id,
        "entity_refs": entity_refs or [],
        "derived_from_ids": derived_from_ids or [],
        "created_at": now,
        "updated_at": now,
        "last_validated_at": now,
        "next_review_at": now,  # placeholder — caller should compute
        "review_cadence_days": cadence,
        "used_count": 0,
        "last_used_at": None,
    }


def build_memory_link(
    *,
    from_id: str,
    to_id: str,
    link_type: str,
    label: str = "",
    current_date: str = "",
) -> dict[str, Any]:
    """Create a link between a memory and an entity or another memory."""
    return {
        "id": f"link-{str(uuid4())[:8]}",
        "from_memory_id": from_id,
        "to_id": to_id,
        "link_type": link_type if link_type in VALID_LINK_TYPES else "entities",
        "label": label,
        "created_at": current_date or date.today().isoformat(),
    }


# ---------------------------------------------------------------------------
# Distillation: classify user text into memory types
# ---------------------------------------------------------------------------

_TYPE_SIGNALS: dict[str, list[str]] = {
    "decision": ["decided", "agreed", "approved", "chose", "selected", "go with", "decision"],
    "assumption": ["assuming", "assumption", "we believe", "expect that", "predicated on"],
    "preference": ["prefer", "preference", "always want", "never want", "standard is"],
    "commitment": ["committed", "promised", "obligation", "deadline", "must deliver", "owe"],
    "risk": ["risk", "concern", "exposure", "downside", "watch out", "threat", "vulnerability"],
    "open_question": ["question", "unclear", "unresolved", "need to find out", "tbd", "open item", "?"],
    "process": ["process", "workflow", "procedure", "steps to", "protocol", "routine"],
    "relationship": ["relationship", "partner", "advisor", "manager", "introduced by", "works with"],
    "counterpoint": ["however", "counterpoint", "on the other hand", "disagree", "pushback", "caveat"],
}


def classify_memory_type(text: str) -> str:
    """Infer the best memory type from text content using keyword signals."""
    lower = text.lower()
    best_type = "source_claim"
    best_count = 0
    for mtype, signals in _TYPE_SIGNALS.items():
        count = sum(1 for s in signals if s in lower)
        if count > best_count:
            best_count = count
            best_type = mtype
    return best_type


def distill_structured_memories(
    entries: list[dict[str, Any]],
    request: dict[str, Any],
) -> list[dict[str, Any]]:
    """Convert flat knowledge entries into structured memory objects.

    Preserves backward compatibility — each entry gets wrapped as a memory
    object with an inferred type, and the original entry fields are kept.
    """
    memories: list[dict[str, Any]] = []
    for entry in entries:
        text = entry.get("content", "") or entry.get("summary", "")
        mtype = classify_memory_type(text)
        mem = build_memory_object(
            memory_type=mtype,
            key_claim=entry.get("summary", text[:200]),
            subject=entry.get("topic", request.get("topic", "")),
            owner_id=entry.get("owner_id", request.get("requester_id", "")),
            team_scope=entry.get("access_scope", request.get("access_scope", "team")),
            organization_name=entry.get("organization_name", request.get("organization_name", "")),
            department=entry.get("department", request.get("department", "")),
            source=entry.get("source", ""),
            source_id=entry.get("id", ""),
            current_date=request.get("current_date", ""),
        )
        # Carry forward original entry fields for backward compat
        mem["original_entry_id"] = entry.get("id", "")
        mem["title"] = entry.get("title", "")
        mem["content"] = text
        memories.append(mem)
    return memories


# ---------------------------------------------------------------------------
# Proactive resurfacing
# ---------------------------------------------------------------------------


def compute_resurfacing_score(
    memory: dict[str, Any],
    query_text: str,
    current_date: str,
) -> float:
    """Score how relevant a memory is for proactive resurfacing.

    Higher scores mean the memory should be surfaced. Range: 0.0-1.0.
    """
    score = 0.0
    # Keyword overlap
    query_terms = set(query_text.lower().split())
    claim_terms = set(memory.get("key_claim", "").lower().split())
    subject_terms = set(memory.get("subject", "").lower().split())
    overlap = len(query_terms & (claim_terms | subject_terms))
    if query_terms:
        score += min(overlap / len(query_terms), 1.0) * 0.4

    # Importance boost
    importance = memory.get("importance_score", "medium")
    if importance == "high":
        score += 0.2
    elif importance == "medium":
        score += 0.1

    # Staleness signal — stale memories with active status need attention
    validity = memory.get("validity_status", "active")
    if validity == "active":
        try:
            last_val = date.fromisoformat(str(memory.get("last_validated_at", current_date))[:10])
            cadence = int(memory.get("review_cadence_days", 30))
            cur = date.fromisoformat(current_date[:10])
            days_since = (cur - last_val).days
            if days_since > cadence:
                score += 0.2  # overdue for validation
        except (ValueError, TypeError):
            pass

    # Type boost — decisions and risks matter more
    mtype = memory.get("memory_type", "")
    if mtype in ("decision", "risk", "commitment"):
        score += 0.1
    elif mtype in ("open_question", "assumption"):
        score += 0.05

    # Usage — less-used memories may be forgotten and need resurfacing
    used = int(memory.get("used_count", 0))
    if used == 0:
        score += 0.1

    return min(score, 1.0)


def find_memories_to_resurface(
    memories: list[dict[str, Any]],
    query_text: str,
    current_date: str,
    threshold: float = 0.3,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Find memories that should be proactively resurfaced for a given query."""
    scored = []
    for mem in memories:
        if mem.get("validity_status") == "retired":
            continue
        s = compute_resurfacing_score(mem, query_text, current_date)
        if s >= threshold:
            scored.append((s, mem))
    scored.sort(key=lambda x: x[0], reverse=True)
    results = []
    for s, mem in scored[:limit]:
        result = {**mem, "resurfacing_score": round(s, 3)}
        results.append(result)
    return results


# ---------------------------------------------------------------------------
# Stale memory validation
# ---------------------------------------------------------------------------


def find_stale_memories(
    memories: list[dict[str, Any]],
    current_date: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Find active memories that are overdue for validation."""
    stale: list[tuple[int, dict[str, Any]]] = []
    for mem in memories:
        if mem.get("validity_status") != "active":
            continue
        try:
            last_val = date.fromisoformat(str(mem.get("last_validated_at", current_date))[:10])
            cadence = int(mem.get("review_cadence_days", 30))
            cur = date.fromisoformat(current_date[:10])
            days_overdue = (cur - last_val).days - cadence
            if days_overdue > 0:
                stale.append((days_overdue, mem))
        except (ValueError, TypeError):
            continue
    stale.sort(key=lambda x: x[0], reverse=True)
    return [
        {**mem, "days_overdue": days, "validation_prompt": _validation_prompt(mem)}
        for days, mem in stale[:limit]
    ]


def _validation_prompt(memory: dict[str, Any]) -> str:
    """Generate a validation question for a stale memory."""
    mtype = memory.get("memory_type", "claim")
    claim = memory.get("key_claim", "")[:100]
    if mtype == "assumption":
        return f"Is this assumption still valid? \"{claim}\""
    if mtype == "decision":
        return f"Does this decision still hold? \"{claim}\""
    if mtype == "risk":
        return f"Is this risk still relevant? \"{claim}\""
    if mtype == "commitment":
        return f"Has this commitment been fulfilled? \"{claim}\""
    if mtype == "open_question":
        return f"Has this been resolved? \"{claim}\""
    return f"Is this still accurate? \"{claim}\""


def validate_memory(
    memory: dict[str, Any],
    *,
    confirmed: bool,
    revised_claim: str = "",
    validator_id: str = "",
    current_date: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply a validation to a memory. Returns (updated_memory, validation_record)."""
    now = current_date or date.today().isoformat()
    validation = {
        "id": f"val-{str(uuid4())[:8]}",
        "memory_id": memory.get("id", ""),
        "validator_id": validator_id,
        "action": "confirmed" if confirmed else "revised" if revised_claim else "retired",
        "previous_claim": memory.get("key_claim", ""),
        "revised_claim": revised_claim if revised_claim else None,
        "validated_at": now,
    }
    updated = {**memory}
    updated["last_validated_at"] = now
    updated["updated_at"] = now
    if confirmed:
        updated["validity_status"] = "active"
    elif revised_claim:
        updated["key_claim"] = revised_claim
        updated["validity_status"] = "active"
    else:
        updated["validity_status"] = "retired"
    return updated, validation


# ---------------------------------------------------------------------------
# Engagement events
# ---------------------------------------------------------------------------


def build_engagement_event(
    *,
    event_type: str,
    memory_id: str = "",
    user_id: str = "",
    detail: str = "",
    current_date: str = "",
) -> dict[str, Any]:
    """Track a memory engagement event (reuse, validation, nudge acted on, etc)."""
    return {
        "id": f"eng-{str(uuid4())[:8]}",
        "event_type": event_type,
        "memory_id": memory_id,
        "user_id": user_id,
        "detail": detail,
        "created_at": current_date or datetime.now(timezone.utc).isoformat(),
    }


def generate_reinforcement_message(
    engagement_events: list[dict[str, Any]],
    memory: dict[str, Any],
) -> str | None:
    """Generate a positive reinforcement message based on engagement data."""
    reuse_count = sum(1 for e in engagement_events if e.get("memory_id") == memory.get("id") and e.get("event_type") == "reuse")
    if reuse_count >= 3:
        return f"Your note \"{memory.get('title', memory.get('key_claim', ''))[:50]}\" has been reused {reuse_count} times across team workflows."
    validations = sum(1 for e in engagement_events if e.get("memory_id") == memory.get("id") and e.get("event_type") == "validation")
    if validations >= 1:
        mtype = memory.get("memory_type", "claim")
        if mtype == "open_question":
            return f"You resolved an open question that was blocking follow-up work."
        if mtype == "assumption":
            return f"You confirmed a stale assumption — future briefs will be more accurate."
    return None


# ---------------------------------------------------------------------------
# Digest and pre-meeting brief generation
# ---------------------------------------------------------------------------


def generate_memory_digest(
    memories: list[dict[str, Any]],
    current_date: str,
    limit: int = 20,
) -> dict[str, Any]:
    """Generate a daily/weekly digest of memory state."""
    stale = find_stale_memories(memories, current_date, limit=5)
    recent = [m for m in memories if m.get("created_at", "") >= current_date[:7]]  # same month
    high_importance = [m for m in memories if m.get("importance_score") == "high" and m.get("validity_status") == "active"]
    open_questions = [m for m in memories if m.get("memory_type") == "open_question" and m.get("validity_status") == "active"]
    risks = [m for m in memories if m.get("memory_type") == "risk" and m.get("validity_status") == "active"]
    return {
        "digest_date": current_date,
        "total_memories": len(memories),
        "active_memories": sum(1 for m in memories if m.get("validity_status") == "active"),
        "stale_memories": stale,
        "recent_additions": recent[:limit],
        "high_importance": high_importance[:limit],
        "open_questions": open_questions[:limit],
        "active_risks": risks[:limit],
    }


def generate_pre_meeting_brief(
    memories: list[dict[str, Any]],
    meeting_topic: str,
    current_date: str,
    limit: int = 10,
) -> dict[str, Any]:
    """Compile relevant memories for a meeting or decision context."""
    resurfaced = find_memories_to_resurface(memories, meeting_topic, current_date, threshold=0.2, limit=limit)
    decisions = [m for m in resurfaced if m.get("memory_type") == "decision"]
    assumptions = [m for m in resurfaced if m.get("memory_type") == "assumption"]
    risks = [m for m in resurfaced if m.get("memory_type") == "risk"]
    open_qs = [m for m in resurfaced if m.get("memory_type") == "open_question"]
    commitments = [m for m in resurfaced if m.get("memory_type") == "commitment"]
    stale_in_scope = [m for m in resurfaced if m.get("validity_status") == "active" and _is_stale(m, current_date)]
    return {
        "meeting_topic": meeting_topic,
        "prepared_at": current_date,
        "prior_decisions": decisions,
        "active_assumptions": assumptions,
        "active_risks": risks,
        "open_questions": open_qs,
        "commitments": commitments,
        "stale_requiring_validation": stale_in_scope,
        "all_relevant": resurfaced,
        "total_surfaced": len(resurfaced),
    }


def _is_stale(memory: dict[str, Any], current_date: str) -> bool:
    try:
        last_val = date.fromisoformat(str(memory.get("last_validated_at", current_date))[:10])
        cadence = int(memory.get("review_cadence_days", 30))
        cur = date.fromisoformat(current_date[:10])
        return (cur - last_val).days > cadence
    except (ValueError, TypeError):
        return False
