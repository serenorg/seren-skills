"""Critical tests for the memory_objects wrappers."""

from __future__ import annotations

import pytest

from family_office_base.memory import MemoryRecord, memory_read, memory_write


def test_memory_write_requires_caller(runner) -> None:
    with pytest.raises(ValueError, match="caller"):
        memory_write(
            MemoryRecord(memory_type="decision", key_claim="x"),
            runner=runner,
            caller="",
        )


def test_memory_write_rejects_empty_key_claim() -> None:
    with pytest.raises(ValueError):
        MemoryRecord(memory_type="decision", key_claim="   ")


def test_memory_write_generates_id_and_populates_provenance(runner) -> None:
    rec = MemoryRecord(
        memory_type="commitment",
        key_claim="CPA will deliver K-1s by April 1",
        subject="entity:smith-trust",
        entity_refs=["entity:smith-trust", "advisor:johnson-cpa"],
    )
    memory_id = memory_write(rec, runner=runner, caller="test_skill.cpa")
    assert memory_id.startswith("memory:commitment-")
    # One INSERT call.
    assert len(runner.calls) == 1
    sql, params = runner.calls[0]
    assert "INSERT INTO memory_objects" in sql
    # source_id defaults to caller when not supplied.
    assert "test_skill.cpa" in params
    # The memory_id we return must match what got inserted.
    assert memory_id in params


def test_memory_read_filters_by_type_and_subject(runner) -> None:
    runner.queue_rows([{"id": "memory:decision-abc"}])
    rows = memory_read(
        runner=runner,
        caller=__name__,
        memory_type="decision",
        subject="entity:smith-trust",
        limit=5,
    )
    assert rows == [{"id": "memory:decision-abc"}]
    sql, params = runner.calls[0]
    assert "memory_type = %s" in sql
    assert "subject = %s" in sql
    assert "decision" in params
    assert "entity:smith-trust" in params


def test_memory_read_rejects_out_of_range_limit(runner) -> None:
    with pytest.raises(ValueError):
        memory_read(runner=runner, caller=__name__, limit=0)
    with pytest.raises(ValueError):
        memory_read(runner=runner, caller=__name__, limit=501)


def test_memory_record_rejects_extra_fields() -> None:
    with pytest.raises(ValueError):
        MemoryRecord.model_validate(
            {
                "memory_type": "decision",
                "key_claim": "ok",
                "ssn": "123-45-6789",  # would allow PII smuggling; must reject
            }
        )
