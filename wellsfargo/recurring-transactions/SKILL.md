---
name: recurring-transactions
description: "Detect and track recurring subscriptions, bills, and regular payments from Wells Fargo transaction data stored in SerenDB."
---

# Wells Fargo Recurring Transactions

## When To Use

- Detect recurring subscriptions, bills, and regular payments from transaction history.
- Track recurring transaction frequency, amounts, and next expected dates.
- Identify spending commitments and subscription creep.
- Persist detected recurring patterns into SerenDB for downstream analysis.

## Prerequisites

- The `bank-statement-processing` skill must have completed at least one successful run with SerenDB sync enabled.
- SerenDB must contain populated `wf_transactions` and `wf_txn_categories` tables.
- At least 3 months of transaction data recommended for accurate detection.

## Safety Profile

- Read-only against SerenDB source tables (`wf_transactions`, `wf_txn_categories`).
- Writes only to dedicated `wf_recurring_*` tables (never modifies upstream data).
- No browser automation required.
- No credentials stored or transmitted.
- All amounts sourced from already-masked account data.

## Workflow Summary

1. `resolve_serendb` connects to SerenDB using the same resolution chain as bank-statement-processing.
2. `query_transactions` fetches categorized transactions for the analysis window.
3. `detect_recurring` groups transactions by normalized payee and amount to find repeating patterns.
4. `score_patterns` assigns confidence scores based on frequency regularity and amount consistency.
5. `render_report` produces Markdown and JSON output files.
6. `persist_patterns` upserts detected recurring patterns into SerenDB.

## Quick Start

1. Install dependencies:

```bash
cd wellsfargo/recurring-transactions
python3 -m pip install -r requirements.txt
cp .env.example .env
cp config.example.json config.json
```

2. Detect recurring transactions from the last 12 months:

```bash
python3 scripts/run.py --config config.json --months 12 --out artifacts/recurring-transactions
```

## Commands

```bash
# Last 12 months (default)
python3 scripts/run.py --config config.json --months 12 --out artifacts/recurring-transactions

# Specific date range
python3 scripts/run.py --config config.json --start 2025-01-01 --end 2025-12-31 --out artifacts/recurring-transactions

# Higher confidence threshold
python3 scripts/run.py --config config.json --months 12 --min-confidence 0.8 --out artifacts/recurring-transactions

# Skip SerenDB persistence (local reports only)
python3 scripts/run.py --config config.json --months 12 --skip-persist --out artifacts/recurring-transactions
```

## Outputs

- Markdown report: `artifacts/recurring-transactions/reports/<run_id>.md`
- JSON report: `artifacts/recurring-transactions/reports/<run_id>.json`
- Pattern export: `artifacts/recurring-transactions/exports/<run_id>.patterns.jsonl`

## SerenDB Tables

- `wf_recurring_runs` - recurring detection runs
- `wf_recurring_patterns` - detected recurring transaction patterns
- `wf_recurring_snapshots` - summary snapshot per run

## Reusable Views

- `v_wf_recurring_latest` - most recent recurring pattern snapshot
- `v_wf_recurring_active` - currently active recurring transactions
