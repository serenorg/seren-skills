---
name: foundation-payout-compliance-monitor
description: "Family office: Computes the 5% distributable amount, tracks qualifying distributions, projects payout gaps, set-aside elections, 4720 exposure, and 990-pf readiness."
---

# Family Office · Foundation Payout Compliance Monitor

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

This is a recurring family-office operator skill, not a memo generator. Run it to operationalize `family-foundation-formation-plan and family-philanthropy-strategic-plan` as a stateful, approval-gated employee.

## When to Use

- monitor foundation 5 percent payout
- check 990-PF readiness
- calculate foundation payout gap
- track qualifying distributions

## Operating Contract

This operator computes the 5% distributable amount, tracks qualifying distributions, projects payout gaps, set-aside elections, 4720 exposure, and 990-PF readiness. It belongs to `Philanthropy & Foundation Operations` in the Citi family-office functions frame and is priority `P0` from roadmap issue #852.

Approval gate: monitor only; CPA and board approval are required before filings or final figures. The agent stages review packets, audit rows, reminders, and draft communications. It does not submit money movement, government filings, binding notices, beneficiary changes, or external instructions without the live gate and human approval.

## Schema Guard

Before any read or write, resolve or create the SerenDB project `family-office-foundation-payout-compliance-monitor` and database `family_office_foundation_payout_compliance_monitor`. Verify or create these tables:

- `foundation_compliance_years`
- `asset_value_history`
- `qualifying_distributions`
- `payout_gap_alerts`

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

- `recompute_payout_requirement`
- `project_qualifying_distribution_gap`
- `draft_board_memo`

## Cloud Deployment

Deploy as a seren-cloud cron or event-triggered worker with the default Python runtime. Suggested cadence: `monthly-recompute-weekly-final-quarter-annual-990pf-rollup`. Do not use Docker, a local pull runner, or customer-specific local paths.

## Functional Verification

For each PR, run:

```bash
python3 -m pytest family-office/foundation-payout-compliance-monitor/tests -q
python3 family-office/foundation-payout-compliance-monitor/scripts/agent.py --functional-test --config family-office/foundation-payout-compliance-monitor/config.example.json
node scripts/build-index.mjs > /tmp/seren-skills-index.json
```

If the functional dry-run cannot return `all_green`, open a GitHub issue with label `bug`, assign it to `taariq`, fix through a new worktree and PR, then rerun this verification.

## Public Repo Rules

Do not commit credentials, live customer records, account numbers, portal exports, generated packets, or real family-office documents. `config.example.json` uses synthetic records only.
