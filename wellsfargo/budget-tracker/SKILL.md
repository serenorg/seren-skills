---
name: budget-tracker
description: "Track budget vs. actual spending by category from Wells Fargo transaction data stored in SerenDB."
---

# Wells Fargo Budget Tracker

## When To Use

- Compare actual spending against budget targets by category.
- Track budget utilization and remaining allowances per period.
- Identify categories where spending exceeds budget limits.
- Persist budget snapshots into SerenDB for trend analysis.

## Prerequisites

- The `bank-statement-processing` skill must have completed at least one successful run with SerenDB sync enabled.
- SerenDB must contain populated `wf_transactions` and `wf_txn_categories` tables.
- Configure budget targets in `config/budget_targets.json`.

## Safety Profile

- Read-only against SerenDB source tables (`wf_transactions`, `wf_txn_categories`).
- Writes only to dedicated `wf_budget_*` tables (never modifies upstream data).
- No browser automation required.
- No credentials stored or transmitted.

## Workflow Summary

1. `resolve_serendb` connects to SerenDB using the same resolution chain as bank-statement-processing.
2. `query_transactions` fetches categorized transactions for the requested period.
3. `aggregate_actuals` sums actual spending by category.
4. `compare_budget` computes budget vs. actual variance per category.
5. `render_report` produces Markdown and JSON output files.
6. `persist_snapshot` upserts the budget snapshot into SerenDB.

## Quick Start

1. Install dependencies:

```bash
cd wellsfargo/budget-tracker
python3 -m pip install -r requirements.txt
cp .env.example .env
cp config.example.json config.json
```

2. Customize budget targets in `config/budget_targets.json`.

3. Run budget comparison for the current month:

```bash
python3 scripts/run.py --config config.json --months 1 --out artifacts/budget-tracker
```

## Commands

```bash
# Current month
python3 scripts/run.py --config config.json --months 1 --out artifacts/budget-tracker

# Last 12 months
python3 scripts/run.py --config config.json --months 12 --out artifacts/budget-tracker

# Specific date range
python3 scripts/run.py --config config.json --start 2025-01-01 --end 2025-12-31 --out artifacts/budget-tracker

# Skip SerenDB persistence
python3 scripts/run.py --config config.json --months 1 --skip-persist --out artifacts/budget-tracker
```

## Outputs

- Markdown report: `artifacts/budget-tracker/reports/<run_id>.md`
- JSON report: `artifacts/budget-tracker/reports/<run_id>.json`
- Category export: `artifacts/budget-tracker/exports/<run_id>.categories.jsonl`

## SerenDB Tables

- `wf_budget_runs` - budget tracking runs
- `wf_budget_categories` - budget vs. actual per category per run
- `wf_budget_snapshots` - summary snapshot per run

## Reusable Views

- `v_wf_budget_latest` - most recent budget snapshot
- `v_wf_budget_over_limit` - categories currently over budget
