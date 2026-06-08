---
name: co-invest-direct-deal-pipeline-crm
description: "Family office: Standardize direct and co-investment intake, policy scoring, stage gates, artifact tracking, and post-close milestone monitoring."
---

# Family Office · Co Invest Direct Deal Pipeline CRM

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

This is a recurring family-office operator skill, not a memo-only helper. Run it as a stateful, approval-gated employee for the workflow described in GitHub issue #943.

## When to Use

- intake direct deal pipeline
- score co-invest opportunities
- run direct investment stage gates
- produce pipeline health roll-up

## Operating Contract

This operator turns informal inbound deal flow into a governed CRM with standardized intake, policy scoring, IC stage gates, required artifacts, post-close value-creation milestones, and pipeline-health analytics. It belongs to `Investment Operations` in the family-office operating model and is priority `P1` from roadmap issue #943.

Approval gate: advancing to IC, requesting information, allocation, close, and post-close external follow-up require human approval. The agent stages review packets, audit rows, reminders, source citations, and draft communications. It does not submit money movement, government filings, binding notices, beneficiary changes, vendor instructions, trading orders, or external communications without the live gate and human approval.

Research context: UBS research points to missed opportunities and inconsistent diligence standards when direct deals arrive through informal networks and email.

## Provisioning Assumption

Assume the user may still need to provision a SerenDB account, set `SEREN_API_KEY`, and install the `seren-mcp` server before live gateway publisher calls work. Treat missing SerenDB access, missing publisher access, or missing Seren Passwords vault grants as setup blockers, not reasons to continue with in-memory state.

## Schema Guard

Before any read or write, resolve or create the SerenDB project `family-office-co-invest-direct-deal-pipeline-crm` and database `family_office_co_invest_direct_deal_pipeline_crm`. Verify or create these tables:

- `deal_intake`
- `investment_policy_rubric`
- `deal_scores`
- `stage_gate_ledger`
- `artifact_checklist`
- `value_creation_milestones`
- `pipeline_health_rollups`

If schema provisioning fails, stop before provider calls. A missing schema is a setup blocker, not a reason to continue in memory.

## Seren Passwords

Use Seren Passwords for named secret references only. Config names the vault and item titles; it must not contain vault IDs, item IDs, raw credentials, or any Glide vault reference. Environment and cloud secret-store values may satisfy `SEREN_API_KEY`; provider credentials should be resolved from the named `Family Office Operations` vault after the operator grants access.

## Gateway Publishers

Use live gateway publishers through `seren-mcp` when available:

- `storage` -> `seren-db`
- `passwords` -> `seren-passwords`
- `outlook` -> `microsoft-outlook`
- `gmail` -> `gmail`
- `drive` -> `google-drive`
- `docs` -> `google-docs`
- `docreader` -> `seren-docreader`
- `sheets` -> `google-sheets`
- `calendar` -> `google-calendar`

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

- `ranked_deal_queue`
- `screen_recommendations`
- `stage_gate_status`
- `required_artifacts`
- `pipeline_health_rollup`
- `post_close_milestone_alerts`

## Handoffs

- `client-sourced-deal-review-memo`
- `manager-dd-direct-co-investment`
- `new-business-diligence-memo`

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

- `ingest_deal_batch`
- `score_against_policy`
- `update_stage_gates`
- `draft_screen_recommendations`
- `produce_pipeline_rollup`

## Cloud Deployment

Deploy as a seren-cloud cron or event-triggered worker with the default Python runtime. Suggested cadence: `daily-inbound-deal-intake-weekly-pipeline-rollup-quarterly-health-review`. Do not use Docker, a local pull runner, or customer-specific local paths.

## Functional Verification

For each PR, run:

```bash
python3 -m pytest family-office/co-invest-direct-deal-pipeline-crm/tests -q
python3 family-office/co-invest-direct-deal-pipeline-crm/scripts/agent.py --functional-test --config family-office/co-invest-direct-deal-pipeline-crm/config.example.json
node scripts/build-index.mjs > /tmp/seren-skills-index.json
```

If the functional dry-run cannot return `all_green`, open a GitHub issue with label `bug`, assign it to `taariq`, fix through a new worktree and PR, then rerun this verification.

## Public Repo Rules

Do not commit credentials, live customer records, account numbers, portal exports, generated packets, or real family-office documents. `config.example.json` uses synthetic records only.
