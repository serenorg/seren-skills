---
name: vendor-analysis
description: "Analyze spending by vendor/merchant from Wells Fargo transaction data stored in SerenDB."
---

# Wells Fargo Vendor Analysis

## When To Use

- Analyze spending patterns by vendor/merchant/payee.
- Rank vendors by total spend, frequency, and average transaction size.
- Identify top merchants and spending concentration.
- Persist vendor analytics into SerenDB for downstream analysis.

## Prerequisites

- The `bank-statement-processing` skill must have completed at least one successful run with SerenDB sync enabled.
- SerenDB must contain populated `wf_transactions` and `wf_txn_categories` tables.

## Safety Profile

- Read-only against SerenDB source tables (`wf_transactions`, `wf_txn_categories`).
- Writes only to dedicated `wf_vendor_*` tables (never modifies upstream data).
- No browser automation required.
- No credentials stored or transmitted.

## Quick Start

```bash
cd wellsfargo/vendor-analysis
python3 -m pip install -r requirements.txt
cp .env.example .env && cp config.example.json config.json
python3 scripts/run.py --config config.json --months 12 --out artifacts/vendor-analysis
```

## Commands

```bash
python3 scripts/run.py --config config.json --months 12 --out artifacts/vendor-analysis
python3 scripts/run.py --config config.json --months 12 --top 20 --out artifacts/vendor-analysis
python3 scripts/run.py --config config.json --months 12 --skip-persist --out artifacts/vendor-analysis
```

## Outputs

- Markdown report: `artifacts/vendor-analysis/reports/<run_id>.md`
- JSON report: `artifacts/vendor-analysis/reports/<run_id>.json`
- Vendor export: `artifacts/vendor-analysis/exports/<run_id>.vendors.jsonl`

## SerenDB Tables

- `wf_vendor_runs` - vendor analysis runs
- `wf_vendor_merchants` - per-vendor spending data per run
- `wf_vendor_snapshots` - summary snapshot per run

## Reusable Views

- `v_wf_vendor_latest` - most recent vendor snapshot
- `v_wf_vendor_top_merchants` - top merchants by spend from latest run
