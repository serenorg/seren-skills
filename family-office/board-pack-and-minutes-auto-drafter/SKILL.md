---
name: board-pack-and-minutes-auto-drafter
description: "Family office: Draft board packs, minutes, decisions logs, and owner-scored action items from meeting transcripts with mandatory human review."
---

# Family Office · Board Pack And Minutes Auto Drafter

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

This is a recurring family-office operator skill, not a memo-only helper. Run it as a stateful, approval-gated employee for the workflow described in GitHub issue #944.

## When to Use

- draft minutes from transcript
- build family meeting board pack
- extract decisions and action owners
- stage meeting packet for approval

## Operating Contract

This operator ingests transcripts from meeting tools, drafts minutes, decisions, action items, and a source-linked board-pack appendix, then cross-references prior minutes for follow-up status before human review. It belongs to `Governance` in the family-office operating model and is priority `P1` from roadmap issue #944.

Approval gate: publishing, distribution, and permanent governance-record updates require human approval. The agent stages review packets, audit rows, reminders, source citations, and draft communications. It does not submit money movement, government filings, binding notices, beneficiary changes, vendor instructions, trading orders, or external communications without the live gate and human approval.

Research context: Deloitte and PwC family-office AI research identify call transcription and meeting-minutes drafting as practical near-term workflows.

## Provisioning Assumption

Assume the user may still need to provision a SerenDB account, set `SEREN_API_KEY`, and install the `seren-mcp` server before live gateway publisher calls work. Treat missing SerenDB access, missing publisher access, or missing Seren Passwords vault grants as setup blockers, not reasons to continue with in-memory state.

## Schema Guard

Before any read or write, resolve or create the SerenDB project `family-office-board-pack-and-minutes-auto-drafter` and database `family_office_board_pack_and_minutes_auto_drafter`. Verify or create these tables:

- `transcript_ingest`
- `minutes_drafts`
- `decisions_log`
- `action_items`
- `source_artifact_index`
- `prior_meeting_followups`
- `approval_ledger`

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
- `models` -> `seren-models`

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

- `draft_minutes_doc`
- `action_item_list`
- `decisions_log`
- `board_pack_appendix`
- `followup_status`

## Handoffs

- `family-meeting-agenda-minutes-template`
- `family-council-cycle-operator`
- `governance-document-version-control`

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

- `ingest_transcript`
- `infer_actions_and_decisions`
- `cross_reference_prior_minutes`
- `draft_board_pack_appendix`
- `stage_human_review`

## Cloud Deployment

Deploy as a seren-cloud cron or event-triggered worker with the default Python runtime. Suggested cadence: `event-driven-after-meeting-transcript-ingest`. Do not use Docker, a local pull runner, or customer-specific local paths.

## Functional Verification

For each PR, run:

```bash
python3 -m pytest family-office/board-pack-and-minutes-auto-drafter/tests -q
python3 family-office/board-pack-and-minutes-auto-drafter/scripts/agent.py --functional-test --config family-office/board-pack-and-minutes-auto-drafter/config.example.json
node scripts/build-index.mjs > /tmp/seren-skills-index.json
```

If the functional dry-run cannot return `all_green`, open a GitHub issue with label `bug`, assign it to `taariq`, fix through a new worktree and PR, then rerun this verification.

## Public Repo Rules

Do not commit credentials, live customer records, account numbers, portal exports, generated packets, or real family-office documents. `config.example.json` uses synthetic records only.
