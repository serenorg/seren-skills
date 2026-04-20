# family-office-base

Shared runtime for the `family-office` skill catalog. Imported by every leaf
skill's `scripts/agent.py`. Not itself a Claude Code skill (no `SKILL.md`).

## Scope

Single-family-office installation. No multi-tenant isolation.

## What's here

| Module | Responsibility |
|---|---|
| `schema_guard` | Idempotent DDL for the 16 canonical object tables + support. Only permitted DDL path. |
| `audit_query` | Only permitted read/write path. Enforces confidentiality filter on every SELECT; logs every call. |
| `confidentiality` | 6 label constants + validation + role-visibility map. |
| `memory` | Thin typed wrappers over the knowledge skill's `memory_objects` table. Knowledge schema is not modified. |

Additional modules (objects/, execution/, artifacts/, etc.) land in later PRs
per the implementation plan.

## Hard rules

1. Do not open a direct `psycopg.connect()` from skill code. Use `audit_query`.
2. Do not run raw DDL. Use `run_schema_guard`.
3. Every `audit_query` call passes `caller=__name__` and a `caller_role`.
4. SELECTs against tenant tables must include a `confidentiality_label IN (...)`
   filter derived from the caller's role.
5. No PII in logs, error text, or test output.

See the full security discipline in the implementation plan, Part 3.

## Install

```bash
pip install -e family-office/_base/
```

## Tests

```bash
pytest family-office/_base/tests/
```

Tests use an injected fake runner by default. For real-Postgres smoke,
set `FAMILY_OFFICE_TEST_PG` to a DSN and install `pytest-postgresql`.
