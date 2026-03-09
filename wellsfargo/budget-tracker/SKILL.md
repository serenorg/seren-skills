---
name: budget-tracker
description: "Compare actual Wells Fargo spending against user-defined monthly budgets per category, calculate variance, and track budget adherence over time."
---

# Budget Tracker

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

## When to Use

- compare budget vs actual spending
- track my budget adherence
- show budget variance by category

## Workflow Summary

1. `resolve_serendb` uses `connector.serendb.connect`
2. `load_budgets` uses `transform.load_budget_definitions`
3. `query_actuals` uses `connector.serendb.query`
4. `compute_variance` uses `transform.compute_budget_variance`
5. `compute_adherence_trend` uses `transform.compute_adherence_trend`
6. `render_report` uses `transform.render`
7. `persist_budget_data` uses `connector.serendb.upsert`
