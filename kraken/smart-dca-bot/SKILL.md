---
name: smart-dca-bot
description: "AI-optimized Kraken DCA bot with single-asset, portfolio, and scanner modes using local direct execution and strict safety controls."
---

# Kraken Smart DCA Bot

AI-assisted dollar-cost averaging (DCA) bot for Kraken with three modes:
- `single_asset`
- `portfolio`
- `scanner`

All trades are executed locally and directly against Kraken REST APIs.

## When to Use

- run smart dca on kraken
- optimize recurring crypto buys
- rebalance dca portfolio allocations
- scan for dca opportunities on kraken

## What This Skill Provides

- Mode 1 (`single_asset`) with 5 strategies:
  - `vwap_optimized`
  - `momentum_dip`
  - `spread_optimized`
  - `time_weighted`
  - `simple`
- Mode 2 (`portfolio`) with target allocations and drift detection
- Mode 3 (`scanner`) with four signal families:
  - `volume_spike`
  - `mean_reversion`
  - `momentum_breakout`
  - `new_listing`
- Direct Kraken API integration (no Seren trading proxy)
- First-run Seren API key auto-registration (`SEREN_API_KEY`)
- Optional SerenDB schema + persistence (`SERENDB_URL`)
- JSONL audit logging (`logs/*.jsonl`)
- Cost-basis lot tracking (`state/cost_basis_lots.json`)
- Dry-run mode by default
- Cron/webhook support (`run_agent_server.py`, `setup_cron.py`)

## Setup

1. Copy `.env.example` to `.env` and fill credentials.
2. Copy `config.example.json` to `config.json`.
3. Install dependencies:
   - `pip install -r requirements.txt`
4. Initialize SerenDB schema (optional, requires `SERENDB_URL`):
   - `python scripts/setup_serendb.py`
5. Run dry mode:
   - `python scripts/agent.py --config config.json`
6. Run live mode (explicit opt-in only):
   - set `"dry_run": false` in `config.json`
   - `python scripts/agent.py --config config.json --allow-live --accept-risk-disclaimer`

## Workflow Summary

1. Validate config and policy caps.
2. Ensure `SEREN_API_KEY` (validate existing or auto-register on first run).
3. Build DCA window and market snapshot(s).
4. Select strategy decision and risk-gate execution.
5. Execute locally to Kraken (or simulate in dry-run).
6. Persist runs, snapshots, scanner signals, and cost-basis lots.
7. Emit JSONL audit events.

## Required Disclaimers

IMPORTANT DISCLAIMERS — READ BEFORE USING

1. NOT FINANCIAL ADVICE: This skill is a software tool, not a financial advisor.
   It does not provide investment, financial, tax, or legal advice. All trading
   decisions are made by you. Consult a licensed financial advisor before investing.

2. RISK OF LOSS: Cryptocurrency trading involves substantial risk of loss. Prices
   can decline significantly. You may lose some or all of your invested capital.
   Only invest money you can afford to lose entirely.

3. NO GUARANTEES: Past performance does not guarantee future results. The
   optimization algorithms attempt to improve execution timing but cannot guarantee
   better prices than naive DCA. Market conditions may render optimizations
   ineffective.

4. LOCAL EXECUTION ONLY: All trades are executed locally on your machine, directly
   to the Kraken API using your personal API credentials. No trades flow through
   Seren Gateway or any third-party intermediary. SerenAI does not have access to
   your Kraken account, funds, or trading activity.

5. API KEY SECURITY: Your Kraken API keys are stored locally in your .env file and
   are never transmitted to SerenAI servers. You are responsible for securing your
   API credentials. Use IP whitelisting and withdrawal restrictions on Kraken.

6. EXCHANGE RISK: This skill depends on Kraken's API availability. Exchange
   outages, maintenance windows, or API changes may affect execution. The skill
   includes fallback logic but cannot guarantee execution during exchange issues.

7. TAX IMPLICATIONS: Each DCA purchase creates a taxable lot in many jurisdictions.
   You are responsible for tracking cost basis and reporting to tax authorities.
   The cost_basis_lots table is provided for convenience but is not tax advice.

8. REGULATORY COMPLIANCE: Cryptocurrency regulations vary by jurisdiction. You are
   responsible for ensuring compliance with all applicable laws and regulations in
   your jurisdiction.

9. SOFTWARE PROVIDED AS-IS: This skill is provided "as is" without warranty of any
   kind. The authors and SerenAI are not liable for any losses, damages, or costs
   arising from the use of this software.
