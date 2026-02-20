# Dry Run Checklist

Use this checklist before every strategy dry run.

## 1) Environment

- Confirm `SEREN_API_KEY` is set.
- Confirm `SERENDB_DSN` points to the user database (`alpaca_short_bot` or intended target).
- Confirm Python dependencies are installed:
  - `python3 -m pip install -r requirements.txt`

## 2) Data + Storage Readiness

- Apply schemas:
  - `python3 scripts/setup_serendb.py --dsn "$SERENDB_DSN"`
- Verify key tables exist:
  - `trading.strategy_runs`
  - `trading.candidate_scores`
  - `trading.order_events`
  - `trading.position_marks_daily`
  - `trading.pnl_daily`
  - learning tables from `self_learning_schema.sql`

## 3) Publisher Readiness

- Ensure these publishers are active and authorized:
  - `alpaca`
  - `sec-filings-intelligence`
  - `google-trends`
  - `perplexity` (preferred)
  - `exa` (news fallback)

## 4) Strategy Config

- Universe size is 30 names (`max_names_scored=30`).
- Planned order cap is 8 names (`max_names_orders=8`).
- Default mode is `paper-sim`.
- `strict_required_feeds=true` for production-like dry runs.

## 5) Execute One Full Dry Run

- Use `scripts/dry_run_prompt.txt` as a single copy/paste prompt.
- Run sequence:
  - `scan`
  - `monitor`
  - `post-close`
  - self-learning bootstrap (`action=full`)

## 6) Validate Outputs

- Confirm selected short basket count is `<= 8`.
- Confirm runs are persisted in `trading.strategy_runs` with `status='completed'`.
- Confirm orders landed in `trading.order_events`.
- Confirm daily marks and PnL landed in:
  - `trading.position_marks_daily`
  - `trading.pnl_daily`
- Confirm learning artifacts exist:
  - `learning_feature_snapshots`
  - `learning_outcome_labels`
  - `learning_policy_versions`
  - `learning_policy_assignments`
  - `learning_events`

## 7) Failure Handling

- If a required publisher fails, run should be blocked when strict mode is on.
- Fix publisher issue, then rerun full dry run.
