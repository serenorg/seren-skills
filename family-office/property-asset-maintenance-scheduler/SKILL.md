---
name: property-asset-maintenance-scheduler
description: "Family office: Runs preventive maintenance and compliance calendars for residences, aircraft, marine, vehicles, art and wine systems, and security or fire systems."
---

# Family Office · Property Asset Maintenance Scheduler

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

This is a recurring family-office operator skill, not a memo generator. Run it to operationalize `leisure-asset-ownership-comparison, real-estate-acquisition-plan, and concierge-private-aviation-plan` as a stateful, approval-gated employee.

## When to Use

- schedule property asset maintenance
- run physical asset maintenance calendar
- check overdue work orders
- dispatch approved preventive maintenance

## Operating Contract

This operator runs preventive maintenance and compliance calendars for residences, aircraft, marine, vehicles, art and wine systems, and security or fire systems. It belongs to `Lifestyle, Concierge & Family-Office Administration` in the Citi family-office functions frame and is priority `P1` from roadmap issue #852.

Approval gate: spend-committing dispatch above threshold requires approval. The agent stages review packets, audit rows, reminders, and draft communications. It does not submit money movement, government filings, binding notices, beneficiary changes, or external instructions without the live gate and human approval.

## Schema Guard

Before any read or write, resolve or create the SerenDB project `family-office-property-asset-maintenance-scheduler` and database `family_office_property_asset_maintenance_scheduler`. Verify or create these tables:

- `asset_register`
- `work_order_ledger`
- `service_history`
- `maintenance_calendar`
- `cost_actuals`

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

- `scan_due_maintenance`
- `open_work_order`
- `log_service_history`

## Cloud Deployment

Deploy as a seren-cloud cron or event-triggered worker with the default Python runtime. Suggested cadence: `daily-due-overdue-scan-weekly-status-seasonal-templates`. Do not use Docker, a local pull runner, or customer-specific local paths.

## Functional Verification

For each PR, run:

```bash
python3 -m pytest family-office/property-asset-maintenance-scheduler/tests -q
python3 family-office/property-asset-maintenance-scheduler/scripts/agent.py --functional-test --config family-office/property-asset-maintenance-scheduler/config.example.json
node scripts/build-index.mjs > /tmp/seren-skills-index.json
```

If the functional dry-run cannot return `all_green`, open a GitHub issue with label `bug`, assign it to `taariq`, fix through a new worktree and PR, then rerun this verification.

## Public Repo Rules

Do not commit credentials, live customer records, account numbers, portal exports, generated packets, or real family-office documents. `config.example.json` uses synthetic records only.
