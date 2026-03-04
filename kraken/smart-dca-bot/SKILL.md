---
name: smart-dca-bot
description: "AI-assisted dollar-cost averaging bot for Kraken with single-asset, portfolio, and opportunity-scanner modes plus strict local-execution safety controls."
---

# Kraken Smart DCA Bot

AI-assisted dollar-cost averaging (DCA) bot for Kraken with three execution modes: single asset, portfolio rebalancing, and opportunity scanner.

This skill is built for local execution. Trades are signed and submitted directly from your device to Kraken.

## When to Use

- run smart dca on kraken
- optimize recurring crypto buys
- rebalance dca portfolio allocations
- scan for dca opportunities on kraken

## What This Skill Provides

- Mode 1 (`single_asset`): recurring buys for one asset with timing score gates.
- Mode 2 (`portfolio`): split each cycle across target allocations.
- Mode 3 (`opportunity_scanner`): keep a base DCA leg and optionally shift capped allocation to opportunistic assets.
- Dry-run-first execution with explicit live-trade guardrails.
- Local run logging to `state/dca_runs.db`.

## Local Execution Model

- Kraken trading credentials remain local (`KRAKEN_API_KEY`, `KRAKEN_API_SECRET`).
- Trade execution path is intended to be direct to Kraken APIs.
- No custody of user funds by Seren.
- `SEREN_API_KEY` is for skill-level integration and telemetry, not order custody.

## Safety Controls

- `dry_run` defaults to `true`.
- Live trading requires both:
  - config with `dry_run: false`
  - CLI flags `--allow-live --accept-risk-disclaimer`
- Policy caps included in the generated config model:
  - max daily spend: `$500`
  - max notional: `$5,000`
  - max slippage: `150` bps
- Opportunity mode caps shift allocation to 40% max per cycle.

## Setup

1. Copy `.env.example` to `.env` and set Kraken + Seren keys.
2. Copy `config.example.json` to `config.json`.
3. Install dependencies: `pip install -r requirements.txt`.
4. Run dry mode:
   - `python scripts/agent.py --config config.json`
5. Run live mode (explicit opt-in):
   - set `"dry_run": false` in `config.json`
   - `python scripts/agent.py --config config.json --allow-live --accept-risk-disclaimer`

## Workflow Summary

1. `validate_request` uses `transform.validate_request`
2. `collect_market_snapshot` uses `transform.collect_market_snapshot`
3. `score_entry_window` uses `transform.score_entry_window`
4. `enforce_risk_controls` uses `transform.enforce_risk_controls`
5. `create_execution_plan` uses `transform.create_dca_execution_plan`
6. `execute_or_schedule` uses `transform.execute_or_schedule`
7. `summarize_cycle` uses `transform.summarize_cycle`

## Disclaimer

Crypto trading involves substantial risk and can result in total loss. This skill is an automation tool and not financial, legal, or tax advice. Past performance does not guarantee future results. You are responsible for exchange compliance, configuration choices, and all executed trades.
