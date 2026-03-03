---
name: vendor-analysis
description: "Group Wells Fargo transactions by normalized vendor name, rank by total spend and frequency, detect spending trends, and produce top vendor reports with month-over-month deltas."
---

# Vendor Analysis

## When to Use

- analyze vendor spending
- show top merchants by spend
- detect vendor spending trends

## Workflow Summary

1. `resolve_serendb` uses `connector.serendb.connect`
2. `query_transactions` uses `connector.serendb.query`
3. `normalize_vendors` uses `transform.normalize_vendor_names`
4. `rank_vendors` uses `transform.rank_vendors`
5. `compute_trends` uses `transform.compute_vendor_trends`
6. `render_report` uses `transform.render`
7. `persist_vendor_data` uses `connector.serendb.upsert`
