---
name: multi-entity-stress-scenario-modeler
description: "Family office: Run rates, market, liquidity, and cap-call stress scenarios across trusts, foundations, operating companies, holdcos, and intercompany funding paths."
---

# Family Office · Multi Entity Stress Scenario Modeler

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

This is a recurring family-office operator skill, not a memo-only helper. Run it as a stateful, approval-gated employee for the workflow described in GitHub issue #949.

## When to Use

- run multi-entity stress scenario
- model cap-call cluster
- check liquidity policy bands
- produce IC scenario book

## Operating Contract

This operator loads entity maps, position snapshots, commitments, and intercompany links, runs out-of-the-box stress scenarios, surfaces liquidity gaps, and drafts an IC-ready scenario book with pass/fail status per liquidity policy band. It belongs to `Investment Operations` in the family-office operating model and is priority `P1` from roadmap issue #949.

Approval gate: funding calls, entity transfers, rebalancing, and external manager instructions require human approval. The agent stages review packets, audit rows, reminders, source citations, and draft communications. It does not submit money movement, government filings, binding notices, beneficiary changes, vendor instructions, trading orders, or external communications without the live gate and human approval.

Research context: PwC identifies multi-entity cash-flow forecasting and scenario modeling as a high-ROI quick win for complex family offices.

## Provisioning Assumption

Assume the user may still need to provision a SerenDB account, set `SEREN_API_KEY`, and install the `seren-mcp` server before live gateway publisher calls work. Treat missing SerenDB access, missing publisher access, or missing Seren Passwords vault grants as setup blockers, not reasons to continue with in-memory state.

## Schema Guard

Before any read or write, resolve or create the SerenDB project `family-office-multi-entity-stress-scenario-modeler` and database `family_office_multi_entity_stress_scenario_modeler`. Verify or create these tables:

- `entity_map`
- `position_snapshots`
- `commitment_cashflows`
- `scenario_definitions`
- `stress_results`
- `liquidity_gap_alerts`
- `intercompany_funding_paths`

If schema provisioning fails, stop before provider calls. A missing schema is a setup blocker, not a reason to continue in memory.

## Seren Passwords

Use Seren Passwords for named secret references only. Config names the vault and item titles; it must not contain vault IDs, item IDs, raw credentials, or any Glide vault reference. Environment and cloud secret-store values may satisfy `SEREN_API_KEY`; provider credentials should be resolved from the named `Family Office Operations` vault after the operator grants access.

## Gateway Publishers

Use live gateway publishers through `seren-mcp` when available:

- `storage` -> `seren-db`
- `passwords` -> `seren-passwords`
- `outlook` -> `microsoft-outlook`
- `drive` -> `google-drive`
- `docs` -> `google-docs`
- `sheets` -> `google-sheets`
- `docreader` -> `seren-docreader`

Before stating that a needed publisher is unavailable, query the live publisher catalog. Publisher availability changes frequently.

## Workflow Summary

1. Normalize the source event into a stable idempotency key.
2. Resolve named Seren Passwords references without hardcoded vault ids or raw credentials.
3. Ensure the SerenDB project, database, and tables exist before any source read.
4. Load the current state tables and source-artifact manifest.
5. Ingest the configured dry-run artifacts or synthetic sample records.
6. Evaluate exception flags, required outputs, citations, and handoff readiness against the approval gate.
7. Persist audit events and review tasks in SerenDB.
8. Route a dry-run digest to the configured review mailbox only.
9. Render an operator summary with next actions, blockers, and handoffs.

## Required Outputs

- `scenario_outputs`
- `liquidity_gap_alerts`
- `policy_pass_fail_book`
- `interentity_funding_paths`
- `commitment_ledger_context`

## Handoffs

- `commitment-cashflow-ledger-operator`
- `cashflow-forecast-worksheet`
- `cash-position-and-liquidity-monitor`

## Dry-Run Behavior

Dry-run is the default. It evaluates the synthetic or supplied control event, emits review-task and audit-event payloads, returns all required output placeholders with source-citation slots, and routes review output to `dry_run_to` only. The default dry-run recipient is `taariq@serendb.com`.

Run:

```bash
python3 scripts/agent.py --functional-test --config config.example.json
python3 scripts/agent.py --once --config config.example.json
```

## Live Gate

Live mode is blocked unless both conditions are true:

- CLI includes `--allow-live`
- `config.json` sets `live_mode: true` and `dry_run: false`

Even then, the operator only marks staged actions executable when `approval_confirmed: true` is present after responsible human review.

## Operator Actions

- `load_entity_position_commitment_files`
- `run_rate_shock`
- `run_equity_drawdown`
- `run_cap_call_cluster`
- `draft_scenario_book`

## Cloud Deployment

Deploy as a seren-cloud cron or event-triggered worker with the default Python runtime. Suggested cadence: `monthly-scenario-refresh-quarterly-ic-book-event-driven-cap-call-cluster`. Do not use Docker, a local pull runner, or customer-specific local paths.

## Functional Verification

For each PR, run:

```bash
python3 -m pytest family-office/multi-entity-stress-scenario-modeler/tests -q
python3 family-office/multi-entity-stress-scenario-modeler/scripts/agent.py --functional-test --config family-office/multi-entity-stress-scenario-modeler/config.example.json
node scripts/build-index.mjs > /tmp/seren-skills-index.json
```

If the functional dry-run cannot return `all_green`, open a GitHub issue with label `bug`, assign it to `taariq`, fix through a new worktree and PR, then rerun this verification.

## Public Repo Rules

Do not commit credentials, live customer records, account numbers, portal exports, generated packets, or real family-office documents. `config.example.json` uses synthetic records only.
