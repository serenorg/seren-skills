# MCP-Native Runbook

This skill should run with Seren MCP tools as the default backend.

## Required MCP Capabilities

- Project/database lifecycle:
  - `list_projects`, `list_branches`, `list_databases`, `create_database`
- SQL execution:
  - `run_sql`, `run_sql_transaction`
- Publisher calls:
  - `call_publisher`

## Target Resources

- Project: `alpaca-sass-short-trader-delta-neutral`
- Database: `alpaca_sass_short_bot_dn`
- Mode: `paper-sim`
- Universe size: `30`
- Order cap: `8`
- Delta-neutral hedge:
  - `hedge_ticker` (default `QQQ`)
  - `hedge_ratio` (default `1.0`)

## Publisher Set

- `alpaca`
- `sec-filings-intelligence`
- `google-trends`
- `perplexity` (fallback: `exa`)

## Persistence Contract

Every run must write to:

- `trading.strategy_runs`
- `trading.candidate_scores`
- `trading.order_events`
- `trading.position_marks_daily`
- `trading.pnl_daily`
- `trading.learning_feature_snapshots`
- `trading.learning_outcome_labels`
- `trading.learning_policy_versions`
- `trading.learning_policy_assignments`
- `trading.learning_events`

## Notes

- Use `scripts/serendb_schema.sql` and `scripts/self_learning_schema.sql` for schema apply.
- Planned order set should include short basket + one long hedge leg.
- Treat Python scripts in this directory as fallback/legacy path when MCP execution is unavailable.
