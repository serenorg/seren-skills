"""Critical-only tests for the SerenDB-backed storage (issue #474).

We are not retesting the in-memory shape — `tests/test_persistence.py`
already covers it via the StubStorage. These tests pin the contract
that distinguishes the durable implementation from the stub:

  1. `agent.main()` instantiates the SerenDB-backed class when
     `SEREN_API_KEY` is set, and the in-memory fallback when it is not.
  2. `insert()` lands a parameterized statement on the seren-db
     `/query` endpoint, not on a Python list.
  3. The first call retries past a 503 cold-start (SerenDB is scale to
     zero) and succeeds once the database wakes.
  4. Schema bootstrap runs once per process and is idempotent on
     re-entry (a second `SerenDBStorage` against the same gateway
     does not re-apply DDL).
  5. `markets_created` reads via SELECT, not a local list. Pins the
     read-path migration from `_count_local_markets` /
     `_load_prior_markets`.

Adding any further test here means duplicating coverage that already
lives in test_persistence.py or test_smoke.py.
"""

from __future__ import annotations

import os
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Local stub: minimal SerenDB-flavored fake gateway.
#
# The shared StubGateway is too strict — every call must be
# pre-registered. Storage bootstrap fires multiple GETs and DDLs we
# don't care to enumerate one-by-one, so the storage tests get a
# permissive sibling that records calls and answers seren-db patterns
# from a small response table. test_persistence.py keeps using the
# strict StubGateway.
# ---------------------------------------------------------------------------


class SerenDbFakeGateway:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.markets_created_rows: list[dict[str, Any]] = []
        self.runs_rows: list[dict[str, Any]] = []
        self._cold_start_remaining = 0
        self._cold_start_filter: str | None = None
        self._project_id = "proj_test"
        self._branch_id = "branch_test"

    def cold_start(self, n: int, *, only_when_sql_contains: str | None = None) -> None:
        """Fire `n` 503s on /query. With `only_when_sql_contains`, only
        statements whose SQL contains that substring (case-insensitive)
        consume cold-start credits; everything else passes through.
        Lets a test target the insert path without burning credits on
        DDL."""
        self._cold_start_remaining = n
        self._cold_start_filter = only_when_sql_contains.lower() if only_when_sql_contains else None

    def call(
        self,
        publisher: str,
        method: str,
        path: str,
        body: Any = None,
        headers: dict | None = None,
    ) -> Any:
        self.calls.append(
            {"publisher": publisher, "method": method.upper(), "path": path, "body": body}
        )
        if publisher != "seren-db":
            raise AssertionError(f"unexpected publisher {publisher!r}")
        # cold-start window: emulate scale-to-zero 503 on /query only.
        if path == "/query" and self._cold_start_remaining > 0:
            sql = ((body or {}).get("query") or "").lower()
            if self._cold_start_filter is None or self._cold_start_filter in sql:
                self._cold_start_remaining -= 1
                raise _Http503("database warming up")
        if method.upper() == "GET" and path == "/projects":
            return {"data": [{"id": self._project_id, "name": "prophet"}]}
        if method.upper() == "GET" and path.startswith("/projects/") and path.endswith("/branches"):
            return {"data": [{"id": self._branch_id, "name": "production", "default": True}]}
        if path == "/query":
            query = (body or {}).get("query") or ""
            stripped = query.strip().lower()
            if stripped.startswith("select"):
                if "from " in stripped and ".markets_created" in stripped:
                    return {"data": list(self.markets_created_rows)}
                if "from " in stripped and ".runs" in stripped:
                    return {"data": list(self.runs_rows)}
                return {"data": []}
            return {"data": []}
        raise AssertionError(f"unexpected seren-db path {method} {path}")

    def calls_to(self, *, method: str | None = None, path: str | None = None) -> list[dict]:
        out = list(self.calls)
        if method is not None:
            out = [c for c in out if c["method"] == method.upper()]
        if path is not None:
            out = [c for c in out if c["path"] == path]
        return out

    def query_calls(self) -> list[dict]:
        return self.calls_to(method="POST", path="/query")


class _Http503(Exception):
    """Stand-in for the urllib HTTPError(503) shape SerenDB returns
    while a scale-to-zero database is waking up."""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_main_uses_serendb_storage_when_api_key_set(monkeypatch) -> None:
    """When SEREN_API_KEY is present, main() must wire a SerenDBStorage
    instance into run_command — not the in-memory stand-in. Without
    this, every cron tick discards persistence (issue #474)."""
    monkeypatch.setenv("SEREN_API_KEY", "test-key")
    monkeypatch.delenv("API_KEY", raising=False)

    import agent

    storage_seen: dict[str, Any] = {}

    def _stub_run_command(_request, *, gateway, storage, transport=None):
        storage_seen["instance"] = storage
        return {"status": "ok", "command": "status"}

    monkeypatch.setattr(agent, "run_command", _stub_run_command)
    monkeypatch.setattr(
        "sys.argv",
        ["agent.py", "--command", "status", "--json-output"],
    )

    rc = agent.main()
    assert rc == 0

    storage = storage_seen["instance"]
    # Whatever the concrete name, it must NOT be the in-memory stand-in.
    assert not isinstance(storage, agent._InMemoryStorage), (
        "main() still selects _InMemoryStorage when SEREN_API_KEY is set; "
        "this is the data-loss bug fixed in issue #474."
    )
    assert type(storage).__name__ == "SerenDBStorage"


def test_main_falls_back_to_in_memory_when_api_key_missing(monkeypatch) -> None:
    """No-auth path must not blow up — keep the in-memory fallback for
    standalone CLI dry-runs. Drop the key explicitly so a leaking env
    in CI does not green-light this test for the wrong reason."""
    monkeypatch.delenv("SEREN_API_KEY", raising=False)
    monkeypatch.delenv("API_KEY", raising=False)

    import agent

    storage_seen: dict[str, Any] = {}

    def _stub_run_command(_request, *, gateway, storage, transport=None):
        storage_seen["instance"] = storage
        return {"status": "ok", "command": "status"}

    monkeypatch.setattr(agent, "run_command", _stub_run_command)
    monkeypatch.setattr(
        "sys.argv",
        ["agent.py", "--command", "status", "--json-output"],
    )

    rc = agent.main()
    assert rc == 0
    assert isinstance(storage_seen["instance"], agent._InMemoryStorage)


def test_serendb_storage_insert_writes_to_query_publisher() -> None:
    """An insert must hit `seren-db` `/query` with an INSERT statement —
    not stay in a Python list."""
    from serendb_storage import SerenDBStorage

    fake = SerenDbFakeGateway()
    storage = SerenDBStorage(gateway=fake, schema_name="prophet_bounty_runner_test")

    storage.insert(
        "markets_created",
        {
            "prophet_market_id": "m1",
            "prophet_market_url": "/markets/m1",
            "polymarket_source_url": "0xpoly1",
            "resolves_at": "2026-05-10T12:00:00Z",
            "prophet_viewer_id": "viewer_1",
            "bounty_id": "bounty_fixture_001",
        },
    )

    insert_calls = [
        c for c in fake.query_calls()
        if "insert" in ((c.get("body") or {}).get("query", "").lower())
    ]
    assert insert_calls, "insert() did not POST any INSERT to seren-db /query"
    sql = insert_calls[-1]["body"]["query"].lower()
    assert "markets_created" in sql
    assert "prophet_bounty_runner_test" in sql


def test_serendb_storage_warmup_retries_past_503_then_succeeds() -> None:
    """SerenDB scales to zero; the first /query after idle returns 503
    until the database wakes (~10–30s). The storage layer must retry
    past a small cold-start window before failing closed, otherwise
    every first cron tick of an idle period drops its writes.

    Constrain the cold-start to fire only on INSERT statements so the
    test is unambiguous about which contract it pins (write durability
    after warmup), not bootstrap noise."""
    from serendb_storage import SerenDBStorage

    fake = SerenDbFakeGateway()
    fake.cold_start(2, only_when_sql_contains="insert into")
    storage = SerenDBStorage(
        gateway=fake,
        schema_name="prophet_bounty_runner_test",
        warmup_max_attempts=4,
        warmup_initial_delay_seconds=0.0,  # don't sleep in tests
    )

    storage.insert(
        "events",
        {"event_type": "test.warmup_ok", "run_id": "r1"},
    )

    insert_attempts = [
        c for c in fake.query_calls()
        if "insert into" in ((c.get("body") or {}).get("query", "").lower())
    ]
    # The seed `runs` row + the actual events row = 2 logical inserts.
    # Cold-start consumes 2 503s on the first INSERT (the seed runs row),
    # so we must see >= 4 attempts total (2 retries + 1 success on
    # the runs seed + 1 success on the events insert).
    assert len(insert_attempts) >= 4, (
        f"expected at least 4 INSERT attempts past cold-start, got {len(insert_attempts)}"
    )
    assert fake._cold_start_remaining == 0, "warmup did not exhaust the cold-start window"


def test_serendb_storage_bootstraps_schema_idempotently() -> None:
    """First instantiation should apply the DDL; a second instance
    against the same project must NOT re-fire the CREATE statements
    (idempotent module-level guard). Re-applying DDL on every cron
    tick wastes warm time and racks up unnecessary publisher cost."""
    from serendb_storage import SerenDBStorage, reset_bootstrap_cache

    reset_bootstrap_cache()
    fake = SerenDbFakeGateway()

    SerenDBStorage(gateway=fake, schema_name="prophet_bounty_runner_test").bootstrap()

    first_ddls = [
        c for c in fake.query_calls()
        if "create " in ((c.get("body") or {}).get("query", "").lower())
    ]
    assert first_ddls, "bootstrap did not apply any DDL"
    first_count = len(first_ddls)

    # Second instance, same gateway / schema → must short-circuit.
    SerenDBStorage(gateway=fake, schema_name="prophet_bounty_runner_test").bootstrap()
    second_ddls = [
        c for c in fake.query_calls()
        if "create " in ((c.get("body") or {}).get("query", "").lower())
    ]
    assert len(second_ddls) == first_count, (
        f"bootstrap re-applied DDL on second call ({first_count} -> {len(second_ddls)})"
    )


def test_serendb_storage_markets_created_reads_via_select() -> None:
    """`storage.markets_created` is read by `_load_prior_markets` and
    `_count_local_markets`. With SerenDB storage it must SELECT, so a
    fresh process actually sees rows from earlier ticks. Without this,
    every submission still folds an empty prior list and the operator's
    reconciler view stays broken across process boundaries."""
    from serendb_storage import SerenDBStorage

    fake = SerenDbFakeGateway()
    fake.markets_created_rows = [
        {"prophet_market_id": "m1", "bounty_id": "b1", "prophet_market_url": "/m1"},
        {"prophet_market_id": "m2", "bounty_id": "b1", "prophet_market_url": "/m2"},
    ]
    storage = SerenDBStorage(gateway=fake, schema_name="prophet_bounty_runner_test")

    rows = storage.markets_created

    assert isinstance(rows, list)
    ids = {r.get("prophet_market_id") for r in rows}
    assert ids == {"m1", "m2"}
    select_calls = [
        c for c in fake.query_calls()
        if "select" in ((c.get("body") or {}).get("query", "").lower())
    ]
    assert select_calls, "markets_created did not issue a SELECT against seren-db"
