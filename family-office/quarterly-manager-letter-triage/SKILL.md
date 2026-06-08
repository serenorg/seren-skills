---
name: quarterly-manager-letter-triage
description: "Family office: Triage GP quarterly letters and K-1 packages, extract material events, diff against prior quarters, and draft principal-facing watch-list summaries."
---

# Family Office · Quarterly Manager Letter Triage

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

This is a recurring family-office operator skill, not a memo-only helper. Run it as a stateful, approval-gated employee for the workflow described in GitHub issue #937.

## When to Use

- triage GP quarterly letters
- summarize manager letter deltas
- find material events in LP updates
- run quarterly manager letter watch-list

## Operating Contract

This operator ingests inbound LP and GP letters, K-1 packages, and related email bodies, extracts material manager events, compares each fund against its prior-quarter language and metrics, and drafts one-page family-principal summaries plus a portfolio watch-list. It belongs to `Investment Operations` in the family-office operating model and is priority `P1` from roadmap issue #937.

Approval gate: principal routing, manager outreach, and investment-committee escalation require human approval. The agent stages review packets, audit rows, reminders, source citations, and draft communications. It does not submit money movement, government filings, binding notices, beneficiary changes, vendor instructions, trading orders, or external communications without the live gate and human approval.

Research context: PwC family-office AI guidance names investment-memo summarization and funding-document term extraction as near-term high-ROI workflows.

## Provisioning Assumption

Assume the user may still need to provision a SerenDB account, set `SEREN_API_KEY`, and install the `seren-mcp` server before live gateway publisher calls work. Treat missing SerenDB access, missing publisher access, or missing Seren Passwords vault grants as setup blockers, not reasons to continue with in-memory state.

## Schema Guard

Before any read or write, resolve or create the SerenDB project `family-office-quarterly-manager-letter-triage` and database `family_office_quarterly_manager_letter_triage`. Verify or create these tables:

- `manager_letter_ingest`
- `fund_period_letters`
- `material_event_ledger`
- `quarterly_delta_log`
- `portfolio_watchlist`
- `k1_package_tracker`
- `source_artifact_citations`

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

- `per_fund_summary`
- `prior_quarter_delta`
- `material_event_register`
- `portfolio_watchlist`
- `manager_fund_monitor_context`

## Handoffs

- `manager-fund-monitor`
- `manager-dd-private-equity`
- `client-sourced-deal-review-memo`

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

- `ingest_quarterly_letters`
- `extract_material_events`
- `diff_prior_quarter`
- `draft_principal_summary`
- `update_watchlist`

## Cloud Deployment

Deploy as a seren-cloud cron or event-triggered worker with the default Python runtime. Suggested cadence: `quarterly-letter-window-daily-ingest-weekly-watchlist`. Do not use Docker, a local pull runner, or customer-specific local paths.

## Functional Verification

For each PR, run:

```bash
python3 -m pytest family-office/quarterly-manager-letter-triage/tests -q
python3 family-office/quarterly-manager-letter-triage/scripts/agent.py --functional-test --config family-office/quarterly-manager-letter-triage/config.example.json
node scripts/build-index.mjs > /tmp/seren-skills-index.json
```

If the functional dry-run cannot return `all_green`, open a GitHub issue with label `bug`, assign it to `taariq`, fix through a new worktree and PR, then rerun this verification.

## Public Repo Rules

Do not commit credentials, live customer records, account numbers, portal exports, generated packets, or real family-office documents. `config.example.json` uses synthetic records only.
