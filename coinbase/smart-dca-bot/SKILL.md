---
name: smart-dca-bot
description: "AI-optimized Coinbase Smart DCA bot with single-asset, portfolio, and opportunity-scanner modes using local direct execution and strict safety controls."
---

# Coinbase Smart DCA Bot

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

AI-assisted dollar-cost averaging (DCA) bot for Coinbase Advanced Trade with three modes:
- `single_asset`
- `portfolio`
- `opportunity_scanner`

All trades execute locally and directly against Coinbase APIs.

## When to Use

- run smart dca on coinbase
- optimize recurring crypto buys
- rebalance dca portfolio allocations
- scan for dca opportunities with approval controls

## What This Skill Provides

- Mode 1 (`single_asset`) with execution strategies:
  - `vwap_optimized`
  - `momentum_dip`
  - `spread_optimized`
  - `time_weighted`
  - `simple`
- Mode 2 (`portfolio`) with target allocations and drift-aware DCA
- Mode 3 (`opportunity_scanner`) with signals:
  - `oversold_rsi`
  - `volume_spike`
  - `mean_reversion`
  - `new_listing`
  - `learn_earn`
- Coinbase-specific route selection (`ASSET-USD` vs `ASSET-USDC`)
- Optional staking context and post-buy staking hints
- First-run Seren API key auto-registration (`SEREN_API_KEY`)
- Optional SerenDB persistence (`SERENDB_URL`)
- JSONL audit logs in `logs/`
- Cost-basis lot tracking in `state/cost_basis_lots.json`
- Dry-run default, explicit live opt-in

## Setup

1. Copy `.env.example` to `.env` and set credentials.
2. Copy `config.example.json` to `config.json`.
3. Install dependencies:
   - `pip install -r requirements.txt`
4. (Optional) initialize SerenDB schema:
   - `python scripts/setup_serendb.py`
5. Run dry mode:
   - `python scripts/agent.py --config config.json --accept-risk-disclaimer`
6. Run live mode (explicit opt-in only):
   - set `"dry_run": false` in `config.json`
   - `python scripts/agent.py --config config.json --allow-live --accept-risk-disclaimer`

## Workflow Summary

1. Validate config and risk policy caps.
2. Ensure `SEREN_API_KEY` (validate existing or auto-register).
3. Build market snapshots and select execution route (`USD` vs `USDC`).
4. Compute strategy decision and risk-gate execution.
5. Execute locally to Coinbase (or simulate in dry-run).
6. Persist runs, snapshots, signals, and cost-basis lots.
7. Emit structured audit logs.

## Required Disclaimers

IMPORTANT DISCLAIMERS — READ BEFORE USING

1. NOT FINANCIAL ADVICE: This skill is automation software, not an advisor.
2. RISK OF LOSS: Crypto trading can lose principal and more in volatile markets.
3. NO GUARANTEES: Optimization logic may not outperform naive scheduled DCA.
4. LOCAL EXECUTION ONLY: Trades run locally and directly against Coinbase.
5. API KEY SECURITY: Coinbase credentials remain local and are never sent to Seren.
6. STAKING RISK: APY varies and staking may involve lockup/slashing risk.
7. REGULATORY/TAX: You are responsible for legal/tax compliance in your jurisdiction.
8. NO AFFILIATION: This skill is not affiliated with or endorsed by Coinbase.
