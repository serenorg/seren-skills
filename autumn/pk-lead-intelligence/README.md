# pk-lead-intelligence

PK Lead Intelligence skill — daily enrichment + weekly status pipeline for
the Packaging division. End-user-facing documentation lives in `SKILL.md`;
this README is for the engineer cloning the repo.

The published catalog slug is `autumn-pk-lead-intelligence` — install via
Seren Desktop's Skills panel or `GET /publishers/seren-skills/skills/autumn-pk-lead-intelligence`.

## Local setup

1. Python 3.11+
2. `cd autumn/pk-lead-intelligence`
3. `python3 -m venv .venv && source .venv/bin/activate`
4. `pip install -r requirements.txt`
5. `playwright install chromium`
6. `cp .env.example .env` and fill in
7. `cp config.example.json config.json` and adjust

See the implementation plan checked in alongside the project for the full
phase-by-phase breakdown.

## Phase status (read before assuming any path is end-to-end)

Phases 3 and 4 are live against the production HU Salesforce org as of
the [#563](https://github.com/serenorg/seren-skills/issues/563) closeout
(2026-05-21). Dry-run enrichment and live Note writes both run behind
the Business Unit -> PACKAGING cross-division check. Live writes
additionally require the `live_mode = true` + `--allow-live` double
gate. The earlier `# pragma: no cover` stubs that raised
`NotImplementedError` have been replaced by the real Lightning DOM
drivers; `SKILL.md` § "Status by Phase" is the canonical state table.

Sandbox test scaffolding is still gated behind the `pytest.mark.sandbox`
marker, and the operator checkpoint runbook stays at
`tests/sandbox/CHECKPOINT_RUNBOOK.md` for the rare manual revalidation
case.

Sandbox-only test scaffolding lives under `tests/sandbox/`. The
`pytest.mark.sandbox` marker is registered in `pyproject.toml` and
skipped by default; the runbook for the operator checkpoint session that
lands the live drivers (and the sandbox tests that exercise them) is at
`tests/sandbox/CHECKPOINT_RUNBOOK.md`. Run those tests with
`pytest -m sandbox` once they land.

<!-- trigger publisher reverse-sync after seren-skills-publisher#42 self-register-folder fix landed 2026-05-22 -->
