---
name: geopolitical-fx-exposure-dashboard
description: "Family office: Compute FX net exposure, country and sanctions exposure, supply-chain concentration, and dry-run hedge recommendations against policy bands."
---

# Family Office · Geopolitical FX Exposure Dashboard

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

This is a recurring family-office operator skill, not a memo-only helper. Run it as a stateful, approval-gated employee for the workflow described in GitHub issue #947.

## When to Use

- calculate family FX exposure
- screen country and sanctions exposure
- draft hedging recommendations
- run geopolitical exposure dashboard

## Operating Contract

This operator computes net currency exposure across assets and operating revenues, maps country and sanctions exposure, flags supply-chain concentrations, and drafts hedging recommendations with policy-band checks. It belongs to `Risk Management` in the family-office operating model and is priority `P1` from roadmap issue #947.

Approval gate: hedge trade routing, position changes, and external advisor instructions require separate human approval. The agent stages review packets, audit rows, reminders, source citations, and draft communications. It does not submit money movement, government filings, binding notices, beneficiary changes, vendor instructions, trading orders, or external communications without the live gate and human approval.

Research context: UBS research emphasizes geopolitical conflict risk and the need for systematic currency diversification and hedging frameworks.

## Provisioning Assumption

Assume the user may still need to provision a SerenDB account, set `SEREN_API_KEY`, and install the `seren-mcp` server before live gateway publisher calls work. Treat missing SerenDB access, missing publisher access, or missing Seren Passwords vault grants as setup blockers, not reasons to continue with in-memory state.

## Schema Guard

Before any read or write, resolve or create the SerenDB project `family-office-geopolitical-fx-exposure-dashboard` and database `family_office_geopolitical_fx_exposure_dashboard`. Verify or create these tables:

- `position_exposure`
- `operating_company_revenue`
- `country_exposure_map`
- `sanctions_screen_results`
- `supply_chain_concentration`
- `hedge_recommendations`
- `policy_band_checks`

If schema provisioning fails, stop before provider calls. A missing schema is a setup blocker, not a reason to continue in memory.

## Seren Passwords

Use Seren Passwords for named secret references only. Config names the vault and item titles; it must not contain vault IDs, item IDs, raw credentials, or any Glide vault reference. Environment and cloud secret-store values may satisfy `SEREN_API_KEY`; provider credentials should be resolved from the named `Family Office Operations` vault after the operator grants access.

## Gateway Publishers

Use live gateway publishers through `seren-mcp` when available:

- `storage` -> `seren-db`
- `passwords` -> `seren-passwords`
- `outlook` -> `microsoft-outlook`
- `drive` -> `google-drive`
- `sheets` -> `google-sheets`
- `docreader` -> `seren-docreader`
- `search` -> `perplexity`
- `exa` -> `exa`
- `news` -> `real-time-news`
- `sanctions` -> `opensanctions`

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

- `fx_net_exposure`
- `country_exposure_dashboard`
- `sanctions_screen`
- `supply_chain_flags`
- `hedge_trade_recommendations`
- `policy_band_check`

## Handoffs

- `cash-position-and-liquidity-monitor`
- `portfolio-risk-register`
- `target-asset-allocation-model`

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

- `calculate_fx_netting`
- `map_country_and_sanctions_exposure`
- `flag_supply_chain_concentration`
- `draft_hedge_recommendations`

## Cloud Deployment

Deploy as a seren-cloud cron or event-triggered worker with the default Python runtime. Suggested cadence: `daily-exposure-refresh-weekly-policy-band-review-event-driven-sanctions-screen`. Do not use Docker, a local pull runner, or customer-specific local paths.

## Functional Verification

For each PR, run:

```bash
python3 -m pytest family-office/geopolitical-fx-exposure-dashboard/tests -q
python3 family-office/geopolitical-fx-exposure-dashboard/scripts/agent.py --functional-test --config family-office/geopolitical-fx-exposure-dashboard/config.example.json
node scripts/build-index.mjs > /tmp/seren-skills-index.json
```

If the functional dry-run cannot return `all_green`, open a GitHub issue with label `bug`, assign it to `taariq`, fix through a new worktree and PR, then rerun this verification.

## Public Repo Rules

Do not commit credentials, live customer records, account numbers, portal exports, generated packets, or real family-office documents. `config.example.json` uses synthetic records only.
