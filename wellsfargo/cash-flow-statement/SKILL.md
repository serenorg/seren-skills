---
name: cash-flow-statement
description: "Generate operating, investing, and financing cash flow statements from Wells Fargo transaction data stored in SerenDB."
---
# Wells Fargo Cash Flow

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

## When To Use

- Generate cash flow statements broken into Operating, Investing, and Financing activities.
- Track net cash position changes over configurable periods.
- Produce human-readable Markdown and machine-readable JSON reports.
- Persist cash flow snapshots into SerenDB for downstream analysis.

## Prerequisites

- The `bank-statement-processing` skill must have completed at least one successful run with SerenDB sync enabled.
- SerenDB must contain populated `wf_transactions` and `wf_txn_categories` tables.

## Safety Profile

- Read-only against SerenDB source tables (`wf_transactions`, `wf_txn_categories`).
- Writes only to dedicated `wf_cashflow_*` tables (never modifies upstream data).
- No browser automation required.
- No credentials stored or transmitted.
- All amounts sourced from already-masked account data.

## Workflow Summary

1. `resolve_serendb` connects to SerenDB using the same resolution chain as bank-statement-processing.
2. `query_transactions` fetches categorized transactions for the requested date range.
3. `classify_activities` maps transaction categories to Operating, Investing, or Financing activities using `config/activity_map.json`.
4. `build_statement` aggregates activities into a cash flow statement.
5. `render_report` produces Markdown and JSON output files.
6. `persist_statement` upserts the cash flow snapshot into SerenDB.

## Quick Start

1. Install dependencies:

```bash
cd wellsfargo/cash-flow-statement
python3 -m pip install -r requirements.txt
cp .env.example .env
cp config.example.json config.json
```

2. Generate a cash flow statement for the last 12 months:

```bash
python3 scripts/run.py --config config.json --months 12 --out artifacts/cash-flow-statement
```

3. Generate a statement for a specific date range:

```bash
python3 scripts/run.py --config config.json --start 2025-01-01 --end 2025-12-31 --out artifacts/cash-flow-statement
```

## Commands

```bash
# Last 12 months (default)
python3 scripts/run.py --config config.json --months 12 --out artifacts/cash-flow-statement

# Specific date range
python3 scripts/run.py --config config.json --start 2025-06-01 --end 2025-12-31 --out artifacts/cash-flow-statement

# Skip SerenDB persistence (local reports only)
python3 scripts/run.py --config config.json --months 12 --skip-persist --out artifacts/cash-flow-statement
```

## Outputs

- Markdown report: `artifacts/cash-flow-statement/reports/<run_id>.md`
- JSON report: `artifacts/cash-flow-statement/reports/<run_id>.json`
- Activity export: `artifacts/cash-flow-statement/exports/<run_id>.activities.jsonl`

## SerenDB Tables

- `wf_cashflow_runs` - cash flow statement generation runs
- `wf_cashflow_activities` - individual activity line items per run
- `wf_cashflow_snapshots` - summary totals per run

## Reusable Views

- `v_wf_cashflow_latest` - most recent cash flow snapshot
- `v_wf_cashflow_by_month` - monthly cash flow breakdown by activity
