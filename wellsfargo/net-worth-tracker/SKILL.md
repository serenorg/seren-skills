---
name: net-worth-tracker
description: "Track net worth trends over time from Wells Fargo transaction data stored in SerenDB."
---

# Wells Fargo Net Worth Tracker

## When To Use

- Track net worth trends from cumulative transaction balances over time.
- Compute monthly inflow/outflow totals and running balance.
- Visualize net worth trajectory across configurable periods.
- Persist net worth snapshots into SerenDB for trend analysis.

## Prerequisites

- The `bank-statement-processing` skill must have completed at least one successful run with SerenDB sync enabled.
- SerenDB must contain populated `wf_transactions` and `wf_txn_categories` tables.

## Safety Profile

- Read-only against SerenDB source tables.
- Writes only to dedicated `wf_networth_*` tables (never modifies upstream data).
- No browser automation required.
- No credentials stored or transmitted.

## Quick Start

```bash
cd wellsfargo/net-worth-tracker
python3 -m pip install -r requirements.txt
cp .env.example .env && cp config.example.json config.json
python3 scripts/run.py --config config.json --months 12 --out artifacts/net-worth-tracker
```

## Commands

```bash
python3 scripts/run.py --config config.json --months 12 --out artifacts/net-worth-tracker
python3 scripts/run.py --config config.json --start 2025-01-01 --end 2025-12-31 --out artifacts/net-worth-tracker
python3 scripts/run.py --config config.json --months 12 --skip-persist --out artifacts/net-worth-tracker
```

## Outputs

- Markdown report: `artifacts/net-worth-tracker/reports/<run_id>.md`
- JSON report: `artifacts/net-worth-tracker/reports/<run_id>.json`
- Monthly export: `artifacts/net-worth-tracker/exports/<run_id>.monthly.jsonl`

## SerenDB Tables

- `wf_networth_runs` - net worth tracking runs
- `wf_networth_monthly` - monthly inflow/outflow/balance per run
- `wf_networth_snapshots` - summary snapshot per run

## Reusable Views

- `v_wf_networth_latest` - most recent net worth snapshot
- `v_wf_networth_trend` - monthly net worth trend from latest run
