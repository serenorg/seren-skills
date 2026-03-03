---
name: net-worth-tracker
description: "Track account balances from Wells Fargo statement data with optional manual asset and liability entries to produce a simplified balance sheet and net worth trajectory over time."
---

# Net Worth Tracker

## When to Use

- track my net worth over time
- generate balance sheet from wells fargo data
- show net worth trajectory

## Workflow Summary

1. `resolve_serendb` uses `connector.serendb.connect`
2. `query_balances` uses `connector.serendb.query`
3. `load_manual_entries` uses `transform.load_manual_entries`
4. `compute_balance_sheet` uses `transform.compute_balance_sheet`
5. `compute_net_worth_trajectory` uses `transform.compute_net_worth_trajectory`
6. `render_report` uses `transform.render`
7. `persist_networth_data` uses `connector.serendb.upsert`
