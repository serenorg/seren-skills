# pk-lead-intelligence

PK Lead Intelligence skill — daily enrichment + weekly status pipeline for
the Packaging division. End-user-facing documentation lives in `SKILL.md`;
this README is for the engineer cloning the repo.

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

Phase 3 (#550) and Phase 4 (#557) ship as `feat(...)` commits, but the
Playwright code that drives Salesforce Lightning is gated behind
`# pragma: no cover` stubs that `raise NotImplementedError`. Pure logic
(gates, idempotency, recency checks, renderers) is real and unit-tested;
the DOM driving is deferred to a sandbox-supervised operator checkpoint
that has not yet happened. See `SKILL.md` § "Status by Phase" and issue
[#563](https://github.com/serenorg/seren-skills/issues/563).

Sandbox-only test scaffolding lives under `tests/sandbox/`. The
`pytest.mark.sandbox` marker is registered in `pyproject.toml` and
skipped by default; the runbook for the operator checkpoint session that
lands the live drivers (and the sandbox tests that exercise them) is at
`tests/sandbox/CHECKPOINT_RUNBOOK.md`. Run those tests with
`pytest -m sandbox` once they land.
