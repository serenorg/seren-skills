---
name: travel-security-keyperson-watch
description: "Family office: Correlates threat feeds and advisories against trips, creates deduped alerts, assembles pre-trip briefs, and tracks key-person business-continuity signals."
---

# Family Office · Travel Security Keyperson Watch

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

This is a recurring family-office operator skill, not a memo generator. Run it to operationalize `concierge-travel-coordination-plan, concierge-personal-protection-plan, and family-risk-management-plan` as a stateful, approval-gated employee.

## When to Use

- run travel security watch
- prepare pre-trip security brief
- check key person travel risk
- correlate threat feeds with itinerary

## Operating Contract

This operator correlates threat feeds and advisories against trips, creates deduped alerts, assembles pre-trip briefs, and tracks key-person business-continuity signals. It belongs to `Risk, Insurance, Cyber & Physical Security` in the Citi family-office functions frame and is priority `P1` from roadmap issue #852.

Approval gate: the agent never cancels travel or dispatches protection without approval. The agent stages review packets, audit rows, reminders, and draft communications. It does not submit money movement, government filings, binding notices, beneficiary changes, or external instructions without the live gate and human approval.

## Schema Guard

Before any read or write, resolve or create the SerenDB project `family-office-travel-security-keyperson-watch` and database `family_office_travel_security_keyperson_watch`. Verify or create these tables:

- `trip_register`
- `threat_indicators`
- `travel_alerts`
- `provider_roster`
- `keyperson_register`

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

- `correlate_trip_threats`
- `draft_pretrip_brief`
- `open_travel_alert`

## Cloud Deployment

Deploy as a seren-cloud cron or event-triggered worker with the default Python runtime. Suggested cadence: `daily-feed-calendar-correlation-pretrip-t7-t2-briefs`. Do not use Docker, a local pull runner, or customer-specific local paths.

## Functional Verification

For each PR, run:

```bash
python3 -m pytest family-office/travel-security-keyperson-watch/tests -q
python3 family-office/travel-security-keyperson-watch/scripts/agent.py --functional-test --config family-office/travel-security-keyperson-watch/config.example.json
node scripts/build-index.mjs > /tmp/seren-skills-index.json
```

If the functional dry-run cannot return `all_green`, open a GitHub issue with label `bug`, assign it to `taariq`, fix through a new worktree and PR, then rerun this verification.

## Public Repo Rules

Do not commit credentials, live customer records, account numbers, portal exports, generated packets, or real family-office documents. `config.example.json` uses synthetic records only.
