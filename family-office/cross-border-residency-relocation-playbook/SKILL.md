---
name: cross-border-residency-relocation-playbook
description: "Family office: Orchestrate a principal residency move with pre-move tax delta modeling, entity reclassification tasks, reporting calendars, and per-asset action lists."
---

# Family Office · Cross Border Residency Relocation Playbook

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

This is a recurring family-office operator skill, not a memo-only helper. Run it as a stateful, approval-gated employee for the workflow described in GitHub issue #940.

## When to Use

- plan a principal residency move
- model cross-border tax delta
- build relocation reporting calendar
- coordinate FATCA CRS reclassification

## Operating Contract

This operator coordinates the dry-run work for principal residency changes across origin and destination jurisdictions, asset classes, trusts, entities, accountants, trustees, and family-office administrators. It belongs to `Tax and Entity Administration` in the family-office operating model and is priority `P1` from roadmap issue #940.

Approval gate: tax filings, identity refreshes, advisor instructions, and government submissions require human approval. The agent stages review packets, audit rows, reminders, source citations, and draft communications. It does not submit money movement, government filings, binding notices, beneficiary changes, vendor instructions, trading orders, or external communications without the live gate and human approval.

Research context: UBS research flags multi-jurisdictional tax and reporting complexity as a top family-office pain point.

## Provisioning Assumption

Assume the user may still need to provision a SerenDB account, set `SEREN_API_KEY`, and install the `seren-mcp` server before live gateway publisher calls work. Treat missing SerenDB access, missing publisher access, or missing Seren Passwords vault grants as setup blockers, not reasons to continue with in-memory state.

## Schema Guard

Before any read or write, resolve or create the SerenDB project `family-office-cross-border-residency-relocation-playbook` and database `family_office_cross_border_residency_relocation_playbook`. Verify or create these tables:

- `principal_profile`
- `entity_residency_map`
- `asset_tax_delta`
- `relocation_calendar`
- `action_checklist`
- `reporting_obligations`
- `advisor_handoff_log`

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

- `tax_delta_model`
- `relocation_playbook`
- `reporting_calendar`
- `per_asset_action_list`
- `advisor_handoff_packet`

## Handoffs

- `tax-strategy-memo`
- `cpa-tax-package-checklist`
- `trust-situs-selection-memo`

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

- `build_pre_move_tax_delta`
- `generate_exit_entry_checklists`
- `refresh_fatca_crs_review_queue`
- `produce_reporting_calendar`

## Cloud Deployment

Deploy as a seren-cloud cron or event-triggered worker with the default Python runtime. Suggested cadence: `event-driven-pre-move-weekly-checklist-until-closeout`. Do not use Docker, a local pull runner, or customer-specific local paths.

## Functional Verification

For each PR, run:

```bash
python3 -m pytest family-office/cross-border-residency-relocation-playbook/tests -q
python3 family-office/cross-border-residency-relocation-playbook/scripts/agent.py --functional-test --config family-office/cross-border-residency-relocation-playbook/config.example.json
node scripts/build-index.mjs > /tmp/seren-skills-index.json
```

If the functional dry-run cannot return `all_green`, open a GitHub issue with label `bug`, assign it to `taariq`, fix through a new worktree and PR, then rerun this verification.

## Public Repo Rules

Do not commit credentials, live customer records, account numbers, portal exports, generated packets, or real family-office documents. `config.example.json` uses synthetic records only.
