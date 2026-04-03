---
name: income-statement
display-name: "Wells Fargo Income Statement"
description: "Generate categorized income statements from Wells Fargo transaction data stored in SerenDB by the bank-statement-processing skill."
---

# Wells Fargo Income Statement

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

## When To Use

- Generate monthly or multi-month income statements from Wells Fargo transaction data.
- Categorize transactions into income and expense line items.
- Produce human-readable Markdown and machine-readable JSON reports.
- Persist income statement snapshots into SerenDB for downstream analysis.

## Prerequisites

- The `bank-statement-processing` skill must have completed at least one successful run with SerenDB sync enabled.
- SerenDB must contain populated `wf_transactions` and `wf_txn_categories` tables.

## Safety Profile

- Read-only against SerenDB source tables (`wf_transactions`, `wf_txn_categories`, `wf_monthly_summary`).
- Writes only to dedicated `wf_income_*` tables (never modifies upstream data).
- No browser automation required.
- No credentials stored or transmitted.
- All amounts sourced from already-masked account data.

## Workflow Summary

1. `resolve_serendb` connects to SerenDB using the same resolution chain as bank-statement-processing.
2. `query_transactions` fetches categorized transactions for the requested date range.
3. `classify_line_items` maps transaction categories to income statement line items using `config/line_item_map.json`.
4. `build_statement` aggregates line items into Income, Expenses, and Net Income sections.
5. `render_report` produces Markdown and JSON output files.
6. `persist_statement` upserts the income statement snapshot into SerenDB.

## Quick Start

1. Install dependencies:

```bash
cd wellsfargo/income-statement
python3 -m pip install -r requirements.txt
cp .env.example .env
cp config.example.json config.json
```

2. Generate an income statement for the last 12 months:

```bash
python3 scripts/run.py --config config.json --months 12 --out artifacts/income-statement
```

3. Generate a statement for a specific date range:

```bash
python3 scripts/run.py --config config.json --start 2025-01-01 --end 2025-12-31 --out artifacts/income-statement
```

## Commands

```bash
# Last 12 months (default)
python3 scripts/run.py --config config.json --months 12 --out artifacts/income-statement

# Specific date range
python3 scripts/run.py --config config.json --start 2025-06-01 --end 2025-12-31 --out artifacts/income-statement

# Single month
python3 scripts/run.py --config config.json --start 2025-11-01 --end 2025-11-30 --out artifacts/income-statement

# Skip SerenDB persistence (local reports only)
python3 scripts/run.py --config config.json --months 12 --skip-persist --out artifacts/income-statement
```

## Outputs

- Markdown report: `artifacts/income-statement/reports/<run_id>.md`
- JSON report: `artifacts/income-statement/reports/<run_id>.json`
- Line-item export: `artifacts/income-statement/exports/<run_id>.line_items.jsonl`

## SerenDB Tables

- `wf_income_runs` - income statement generation runs
- `wf_income_line_items` - individual line items per run
- `wf_income_snapshots` - summary totals per run

## Reusable Views

- `v_wf_income_latest` - most recent income statement snapshot
- `v_wf_income_by_month` - monthly income/expense breakdown
