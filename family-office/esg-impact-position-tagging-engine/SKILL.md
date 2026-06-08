---
name: esg-impact-position-tagging-engine
description: "Family office: Tag positions for SDGs, emissions intensity, controversies, exclusion-list status, mandate alignment, and quarterly impact reporting."
---

# Family Office · ESG Impact Position Tagging Engine

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

This is a recurring family-office operator skill, not a memo-only helper. Run it as a stateful, approval-gated employee for the workflow described in GitHub issue #948.

## When to Use

- tag portfolio ESG impact
- check mandate exclusion breaches
- draft quarterly impact report
- score position-level impact data

## Operating Contract

This operator tags positions with impact and ESG attributes, merges third-party and public-company source data where available, checks against the family mandate, and drafts a quarterly impact report with source citations. It belongs to `Investment Operations` in the family-office operating model and is priority `P2` from roadmap issue #948.

Approval gate: investment action, mandate waiver, and external impact-report distribution require human approval. The agent stages review packets, audit rows, reminders, source citations, and draft communications. It does not submit money movement, government filings, binding notices, beneficiary changes, vendor instructions, trading orders, or external communications without the live gate and human approval.

Research context: UBS research says inconsistent ESG data and nonstandard impact metrics undermine credible family and stakeholder reporting.

## Provisioning Assumption

Assume the user may still need to provision a SerenDB account, set `SEREN_API_KEY`, and install the `seren-mcp` server before live gateway publisher calls work. Treat missing SerenDB access, missing publisher access, or missing Seren Passwords vault grants as setup blockers, not reasons to continue with in-memory state.

## Schema Guard

Before any read or write, resolve or create the SerenDB project `family-office-esg-impact-position-tagging-engine` and database `family_office_esg_impact_position_tagging_engine`. Verify or create these tables:

- `position_tags`
- `mandate_rules`
- `third_party_esg_sources`
- `impact_rollups`
- `exclusion_breaches`
- `quarterly_report_drafts`
- `source_citations`

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
- `sheets` -> `google-sheets`
- `docreader` -> `seren-docreader`
- `search` -> `perplexity`
- `exa` -> `exa`
- `sec` -> `sec-filings-intelligence`

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

- `tagged_positions`
- `rollup_scores`
- `quarterly_impact_report`
- `exclusion_breach_list`
- `source_citations`

## Handoffs

- `esg-impact-investing-mandate`
- `manager-dd-esg-impact`

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

- `tag_position_sdg_emissions_controversy`
- `merge_third_party_data`
- `compare_to_mandate`
- `draft_quarterly_report`

## Cloud Deployment

Deploy as a seren-cloud cron or event-triggered worker with the default Python runtime. Suggested cadence: `monthly-position-tag-refresh-quarterly-impact-report`. Do not use Docker, a local pull runner, or customer-specific local paths.

## Functional Verification

For each PR, run:

```bash
python3 -m pytest family-office/esg-impact-position-tagging-engine/tests -q
python3 family-office/esg-impact-position-tagging-engine/scripts/agent.py --functional-test --config family-office/esg-impact-position-tagging-engine/config.example.json
node scripts/build-index.mjs > /tmp/seren-skills-index.json
```

If the functional dry-run cannot return `all_green`, open a GitHub issue with label `bug`, assign it to `taariq`, fix through a new worktree and PR, then rerun this verification.

## Public Repo Rules

Do not commit credentials, live customer records, account numbers, portal exports, generated packets, or real family-office documents. `config.example.json` uses synthetic records only.
