"""Typed wrappers over the existing knowledge skill's memory_objects table.

Do not modify the knowledge skill's schema. These are read/write helpers only,
aligned to the columns the knowledge skill already defines:

    id, memory_type, key_claim, subject, owner_id, team_scope,
    organization_name, department, confidence_score, importance_score,
    validity_status, source, source_id, entity_refs, derived_from_ids,
    review_cadence_days, used_count, last_used_at, last_validated_at,
    next_review_at, created_at, updated_at

The knowledge database is a separate Seren database from the family-office
database. Callers inject a runner bound to the knowledge database.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

MemoryType = Literal[
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
]


class MemoryRecord(BaseModel):
    """A single memory write payload. Provenance fields are populated
    automatically in `memory_write` if not supplied explicitly."""

    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    memory_type: MemoryType
    key_claim: str
    subject: str | None = None
    owner_id: str | None = None
    organization_name: str | None = None
    department: str | None = None
    confidence_score: str = "medium"
    importance_score: str = "medium"
    source: str = "family_office_skill"
    source_id: str | None = None
    entity_refs: list[str] = Field(default_factory=list)
    derived_from_ids: list[str] = Field(default_factory=list)
    review_cadence_days: int = 30

    @field_validator("key_claim")
    @classmethod
    def _key_claim_nonempty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("key_claim must be a non-empty string")
        return v.strip()


class _Runner(Protocol):
    def run(
        self, sql: str, params: tuple | None
    ) -> tuple[list[dict[str, Any]], int]: ...


def _memory_id(memory_type: str) -> str:
    return f"memory:{memory_type}-{uuid.uuid4().hex[:8]}"


def memory_write(
    record: MemoryRecord,
    *,
    runner: _Runner,
    caller: str,
) -> str:
    """Insert a memory_objects row. Returns the memory id.

    Caller is required; it is written to `source_id` if not otherwise supplied,
    and is useful for grepping origins. Validity is set to 'active' and the
    standard provenance fields (last_validated_at, next_review_at) are set.
    """
    if not caller:
        raise ValueError("caller is required for memory_write")

    memory_id = record.id or _memory_id(record.memory_type)
    now = datetime.now(timezone.utc)
    source_id = record.source_id or caller

    sql = (
        "INSERT INTO memory_objects "
        "(id, memory_type, key_claim, subject, owner_id, team_scope, "
        "organization_name, department, confidence_score, importance_score, "
        "validity_status, source, source_id, entity_refs, derived_from_ids, "
        "review_cadence_days, last_validated_at, next_review_at, "
        "created_at, updated_at) "
        "VALUES (%s,%s,%s,%s,%s,'team',%s,%s,%s,%s,'active',%s,%s,%s,%s,%s,%s,"
        "%s + (%s || ' days')::interval,%s,%s)"
    )
    params = (
        memory_id,
        record.memory_type,
        record.key_claim,
        record.subject,
        record.owner_id,
        record.organization_name,
        record.department,
        record.confidence_score,
        record.importance_score,
        record.source,
        source_id,
        record.entity_refs,
        record.derived_from_ids,
        record.review_cadence_days,
        now,
        now,
        str(record.review_cadence_days),
        now,
        now,
    )
    runner.run(sql, params)
    return memory_id


def memory_read(
    *,
    runner: _Runner,
    caller: str,
    memory_type: MemoryType | None = None,
    subject: str | None = None,
    entity_ref: str | None = None,
    limit: int = 25,
) -> list[dict[str, Any]]:
    """Query memory_objects by type / subject / entity_ref.

    The knowledge skill's validity_status is filtered to 'active' by default;
    superseded memories are not returned. Results are ordered by
    last_validated_at DESC.
    """
    if not caller:
        raise ValueError("caller is required for memory_read")
    if limit < 1 or limit > 500:
        raise ValueError("limit must be between 1 and 500")

    clauses = ["validity_status = 'active'"]
    params: list[Any] = []
    if memory_type:
        clauses.append("memory_type = %s")
        params.append(memory_type)
    if subject:
        clauses.append("subject = %s")
        params.append(subject)
    if entity_ref:
        clauses.append("%s = ANY(entity_refs)")
        params.append(entity_ref)

    where = " AND ".join(clauses)
    sql = (
        f"SELECT id, memory_type, key_claim, subject, owner_id, "
        f"confidence_score, importance_score, source, source_id, "
        f"entity_refs, last_validated_at, next_review_at "
        f"FROM memory_objects WHERE {where} "
        f"ORDER BY last_validated_at DESC LIMIT %s"
    )
    params.append(limit)
    rows, _ = runner.run(sql, tuple(params))
    return rows
