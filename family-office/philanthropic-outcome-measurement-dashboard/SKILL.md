---
name: philanthropic-outcome-measurement-dashboard
description: "Family office: Track grant outcomes, KPI evidence, evaluator inputs, theory-of-change drift, and family-facing philanthropic impact dashboards."
---

# Family Office · Philanthropic Outcome Measurement Dashboard

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

This is a recurring family-office operator skill, not a memo-only helper. Run it as a stateful, approval-gated employee for the workflow described in GitHub issue #945.

## When to Use

- measure philanthropic outcomes
- update grant KPI dashboard
- flag theory-of-change drift
- summarize evaluator inputs

## Operating Contract

This operator tracks per-grant theories of change, KPI definitions, grantee reports, evaluator inputs, and drift alerts, then drafts a family-facing impact dashboard without initiating disbursements. It belongs to `Philanthropy` in the family-office operating model and is priority `P1` from roadmap issue #945.

Approval gate: grantee outreach, disbursement changes, and family distribution require human approval. The agent stages review packets, audit rows, reminders, source citations, and draft communications. It does not submit money movement, government filings, binding notices, beneficiary changes, vendor instructions, trading orders, or external communications without the live gate and human approval.

Research context: UBS research notes rising family interest in measuring philanthropic effectiveness and a shortage of systematic internal capability.

## Provisioning Assumption

Assume the user may still need to provision a SerenDB account, set `SEREN_API_KEY`, and install the `seren-mcp` server before live gateway publisher calls work. Treat missing SerenDB access, missing publisher access, or missing Seren Passwords vault grants as setup blockers, not reasons to continue with in-memory state.

## Schema Guard

Before any read or write, resolve or create the SerenDB project `family-office-philanthropic-outcome-measurement-dashboard` and database `family_office_philanthropic_outcome_measurement_dashboard`. Verify or create these tables:

- `grant_portfolio`
- `kpi_definitions`
- `grantee_report_extracts`
- `evaluator_inputs`
- `outcome_dashboard`
- `drift_alerts`
- `source_citations`

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
- `search` -> `perplexity`
- `exa` -> `exa`

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

- `impact_dashboard`
- `drift_alerts`
- `kpi_source_citations`
- `evaluator_input_summary`
- `disbursement_handoff`

## Handoffs

- `grant-pipeline-disbursement-operator`
- `family-philanthropy-strategic-plan`
- `foundation-payout-compliance-monitor`

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

- `ingest_grant_kpis`
- `extract_report_updates`
- `merge_evaluator_inputs`
- `score_theory_of_change_drift`
- `draft_family_dashboard`

## Cloud Deployment

Deploy as a seren-cloud cron or event-triggered worker with the default Python runtime. Suggested cadence: `monthly-grantee-report-ingest-quarterly-impact-dashboard`. Do not use Docker, a local pull runner, or customer-specific local paths.

## Functional Verification

For each PR, run:

```bash
python3 -m pytest family-office/philanthropic-outcome-measurement-dashboard/tests -q
python3 family-office/philanthropic-outcome-measurement-dashboard/scripts/agent.py --functional-test --config family-office/philanthropic-outcome-measurement-dashboard/config.example.json
node scripts/build-index.mjs > /tmp/seren-skills-index.json
```

If the functional dry-run cannot return `all_green`, open a GitHub issue with label `bug`, assign it to `taariq`, fix through a new worktree and PR, then rerun this verification.

## Public Repo Rules

Do not commit credentials, live customer records, account numbers, portal exports, generated packets, or real family-office documents. `config.example.json` uses synthetic records only.
