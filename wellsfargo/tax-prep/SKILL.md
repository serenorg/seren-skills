---
name: tax-prep
description: "Map Wells Fargo transaction categories to IRS tax line items, calculate estimated quarterly payments, flag deductible expenses, and produce a tax-ready summary with totals per line item."
---
# Wells Fargo Tax Prep

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

## When to Use

- prepare tax summary from wells fargo data
- calculate estimated quarterly taxes
- categorize deductible expenses

## Workflow Summary

1. `resolve_serendb` uses `connector.serendb.connect`
2. `query_transactions` uses `connector.serendb.query`
3. `map_tax_line_items` uses `transform.map_to_tax_lines`
4. `flag_deductions` uses `transform.flag_deductible_expenses`
5. `compute_quarterly_estimates` uses `transform.compute_quarterly_estimates`
6. `render_report` uses `transform.render`
7. `persist_tax_data` uses `connector.serendb.upsert`
