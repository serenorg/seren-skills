---
name: cyber-incident-response-runbook
description: "Family office: Produce approval-gated incident runbooks for account takeover, ransomware, vendor breach, lost device, and deepfake voice-clone events."
---

# Family Office · Cyber Incident Response Runbook

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

This is a recurring family-office operator skill, not a memo-only helper. Run it as a stateful, approval-gated employee for the workflow described in GitHub issue #941.

## When to Use

- run family office cyber incident response
- respond to compromised principal account
- stage credential rotation tasks
- draft incident principal notification

## Operating Contract

This operator classifies live cyber incidents, stages account freezes and credential rotations, preserves evidence, drafts principal and vendor notifications, and prepares a post-incident review packet without sending external instructions automatically. It belongs to `Risk Management` in the family-office operating model and is priority `P0` from roadmap issue #941.

Approval gate: outbound communications, account freezes, vendor instructions, and regulator or law-enforcement reports require human approval. The agent stages review packets, audit rows, reminders, source citations, and draft communications. It does not submit money movement, government filings, binding notices, beneficiary changes, vendor instructions, trading orders, or external communications without the live gate and human approval.

Research context: UBS research highlights cybersecurity vulnerabilities in family-office infrastructure, including MFA gaps, vendor access, and home-office device risk.

## Provisioning Assumption

Assume the user may still need to provision a SerenDB account, set `SEREN_API_KEY`, and install the `seren-mcp` server before live gateway publisher calls work. Treat missing SerenDB access, missing publisher access, or missing Seren Passwords vault grants as setup blockers, not reasons to continue with in-memory state.

## Schema Guard

Before any read or write, resolve or create the SerenDB project `family-office-cyber-incident-response-runbook` and database `family_office_cyber_incident_response_runbook`. Verify or create these tables:

- `incident_register`
- `containment_tasks`
- `credential_rotation_ledger`
- `evidence_preservation_log`
- `notification_templates`
- `post_incident_review`

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

- `incident_runbook`
- `containment_task_list`
- `evidence_collection_checklist`
- `principal_notification_draft`
- `post_incident_review_template`

## Handoffs

- `credential-hygiene-breach-monitor`
- `password-management-setup-plan`
- `family-risk-management-plan`

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

- `classify_incident`
- `stage_containment_tasks`
- `generate_evidence_checklist`
- `draft_principal_notification`
- `open_post_incident_review`

## Cloud Deployment

Deploy as a seren-cloud cron or event-triggered worker with the default Python runtime. Suggested cadence: `event-driven-incident-response-post-incident-review`. Do not use Docker, a local pull runner, or customer-specific local paths.

## Functional Verification

For each PR, run:

```bash
python3 -m pytest family-office/cyber-incident-response-runbook/tests -q
python3 family-office/cyber-incident-response-runbook/scripts/agent.py --functional-test --config family-office/cyber-incident-response-runbook/config.example.json
node scripts/build-index.mjs > /tmp/seren-skills-index.json
```

If the functional dry-run cannot return `all_green`, open a GitHub issue with label `bug`, assign it to `taariq`, fix through a new worktree and PR, then rerun this verification.

## Public Repo Rules

Do not commit credentials, live customer records, account numbers, portal exports, generated packets, or real family-office documents. `config.example.json` uses synthetic records only.
