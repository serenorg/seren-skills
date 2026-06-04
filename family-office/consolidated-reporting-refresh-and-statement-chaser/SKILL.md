---
name: consolidated-reporting-refresh-and-statement-chaser
description: "Family office: Refreshes consolidated reports from reconciled gl and position data, generates recipient views, distributes behind review, and chases missing statements and k-1s."
---

# Family Office · Consolidated Reporting Refresh And Statement Chaser

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

This is a recurring family-office operator skill, not a memo generator. Run it to operationalize `consolidated-reporting-spec and cpa-tax-package-checklist` as a stateful, approval-gated employee.

## When to Use

- refresh consolidated family office report
- chase missing statements
- prepare recipient reporting views
- run reporting package review

## Operating Contract

This operator refreshes consolidated reports from reconciled GL and position data, generates recipient views, distributes behind review, and chases missing statements and K-1s. It belongs to `Reporting, Accounting, Cash Management & Bill-Pay` in the Citi family-office functions frame and is priority `P1` from roadmap issue #852.

Approval gate: external report distribution and outbound chasers are review-gated. The agent stages review packets, audit rows, reminders, and draft communications. It does not submit money movement, government filings, binding notices, beneficiary changes, or external instructions without the live gate and human approval.

## Schema Guard

Before any read or write, resolve or create the SerenDB project `family-office-consolidated-reporting-refresh-and-statement-chaser` and database `family_office_consolidated_reporting_refresh_and_statement_chaser`. Verify or create these tables:

- `report_versions`
- `expected_documents`
- `distribution_log`
- `statement_chase_tasks`

If schema provisioning fails, stop before provider calls. A missing schema is a setup blocker, not a reason to continue in memory.

## Seren Passwords

Use Seren Passwords for named secret references only. Config names the vault and item titles; it must not contain vault IDs, item IDs, raw credentials, or any Glide vault reference. Environment and cloud secret-store values may satisfy `SEREN_API_KEY`; provider credentials should be resolved from the named `Family Office Operations` vault after the operator grants access.

## Workflow Summary

1. Normalize the event or obligation into a stable idempotency key.
2. Resolve named Seren Passwords references without hardcoded vault IDs.
3. Ensure the operator SerenDB project, database, and tables exist.
4. Load the relevant register and recent source events.
5. Evaluate exception flags against the approval gate.
6. Persist the audit event and open review tasks.
7. Send a dry-run digest to the configured review mailbox.
8. Render an operator summary with next actions.

## Dry-Run Behavior

Dry-run is the default. It evaluates the synthetic or supplied control event, emits review-task and audit-event payloads, and routes review output to `dry_run_to` only. The default dry-run recipient is `taariq@serendb.com`.

Run:

```bash
python3 scripts/agent.py --functional-test --config config.example.json
python3 scripts/agent.py --once --config config.example.json
```

## Live Gate

Live mode is blocked unless both conditions are true:

- CLI includes `--allow-live`
- `config.json` sets `live_mode: true` and `dry_run: false`

Even then, the operator only marks the staged action executable when `approval_confirmed: true` is present after the responsible human review.

## Operator Actions

- `refresh_report_snapshot`
- `open_statement_chase`
- `stage_distribution_review`

## Cloud Deployment

Deploy as a seren-cloud cron or event-triggered worker with the default Python runtime. Suggested cadence: `report-cadence-refresh-weekly-document-chase-tax-season-intensified`. Do not use Docker, a local pull runner, or customer-specific local paths.

## Functional Verification

For each PR, run:

```bash
python3 -m pytest family-office/consolidated-reporting-refresh-and-statement-chaser/tests -q
python3 family-office/consolidated-reporting-refresh-and-statement-chaser/scripts/agent.py --functional-test --config family-office/consolidated-reporting-refresh-and-statement-chaser/config.example.json
node scripts/build-index.mjs > /tmp/seren-skills-index.json
```

If the functional dry-run cannot return `all_green`, open a GitHub issue with label `bug`, assign it to `taariq`, fix through a new worktree and PR, then rerun this verification.

## Public Repo Rules

Do not commit credentials, live customer records, account numbers, portal exports, generated packets, or real family-office documents. `config.example.json` uses synthetic records only.
