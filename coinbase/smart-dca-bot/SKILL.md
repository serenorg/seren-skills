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

## On Invoke

**Immediately run a dry-run DCA cycle without asking.** Do not present a menu of modes or strategies. Execute:

```bash
cd ~/.config/seren/skills/smart-dca-bot && source .venv/bin/activate && python3 scripts/agent.py --config config.json --accept-risk-disclaimer
```

Display the full dry-run results to the user. Only after results are displayed, present available next steps (live mode, strategy changes). If the user explicitly requests a specific mode in their invocation message, run that mode instead.

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

## Trade Execution Contract

When the user says `sell`, `close`, `exit`, `unwind`, or `flatten`, stop new DCA entries immediately, cancel tracked pending Coinbase orders, and ask only the minimum clarifying question needed if the user also wants held spot inventory liquidated.

## Pre-Trade Checklist

Before any live `run --allow-live --accept-risk-disclaimer` or `loop --allow-live --accept-risk-disclaimer` execution:

1. Verify `SEREN_API_KEY` and Coinbase API credentials are loaded.
2. Verify the selected route, balances, and cash reserve support the requested notional.
3. Verify Python dependencies from `requirements.txt` are installed and the venue client can load.
4. If any credential, dependency, or balance probe fails, stop here and fail closed instead of placing orders.

## Dependency Validation

Dependency validation is required before live trading. Verify `SEREN_API_KEY`, Coinbase credentials, and Python dependencies from `requirements.txt` are installed and loaded. `SERENDB_URL` is optional, but if exchange credentials are missing or the Coinbase client cannot be initialized, the runtime must stop with an error instead of submitting orders.

## Emergency Exit Path

To stop trading immediately, run `python scripts/agent.py stop-trading --config config.json`. The stop-trading path cancels tracked pending Coinbase orders without asking for an extra live confirmation, writes the remaining local state to disk, and leaves held spot positions untouched until the operator chooses how to liquidate them.

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

## Seren-Cron Integration

Use `seren-cron` to run this skill on a schedule — no terminal windows to keep open, no daemons, no permanent computer changes required. Seren-cron is a cloud scheduler that calls your local trigger server on a cron schedule.

**Requirements:** Seren Desktop login or a valid `SEREN_API_KEY`.

### Step 1 — Check seren-cron is available

Before scheduling, verify the publisher is reachable using `mcp__seren__call_publisher`:

```text
publisher: seren-cron
path:      /health
method:    GET
```

If this call fails, **stop here** and tell the user:

> "The seren-cron service could not be reached. Please send this error to <hello@serendb.com> for support."

### Step 2 — Review active cron jobs (always do this first)

**Always check for existing scheduled jobs before creating a new one.** A user may have forgotten a live job is already running.

```text
publisher: seren-cron
path:      /jobs
method:    GET
```

If jobs for this skill already exist, show them to the user and ask:

> "You have [N] active cron job(s) for this skill. Would you like to:
>
> 1. Keep them running (recommended if intentional)
> 2. Stop all and create a new schedule
> 3. Cancel"

**Do not create a duplicate cron job without explicit user confirmation.**

### Step 3 — Start the local trigger server

Start the webhook server that seren-cron will call on each scheduled tick:

```bash
SEREN_API_KEY="$SEREN_API_KEY" python3 scripts/run_agent_server.py --config config.json --port 8080
```

This process runs in your terminal session. When you close the terminal, it stops — **that is expected and correct**. Seren-cron handles the scheduling; your local server handles execution.

### Step 4 — Create the cron schedule

With the server running, create the scheduled job:

```text
publisher: seren-cron
path:      /jobs
method:    POST
body: {
  "name":            "coinbase-smart-dca-live",
  "url":             "http://localhost:8080/run",
  "method":          "POST",
  "cron_expression": "0 */6 * * *",
  "timezone":        "UTC",
  "enabled":         true,
  "timeout_seconds": 60
}
```

Save the returned `job_id` — you need it to pause, resume, or delete the job later.

### Step 5 — Manage the schedule

**List all active jobs:**

```text
publisher: seren-cron, path: /jobs, method: GET
```

**Pause:**

```text
publisher: seren-cron, path: /jobs/{job_id}/pause, method: POST
```

**Resume:**

```text
publisher: seren-cron, path: /jobs/{job_id}/resume, method: POST
```

**Stop permanently:**

```text
publisher: seren-cron, path: /jobs/{job_id}, method: DELETE
```

### Insufficient Funds Guard

If a live trade or cycle fails because the trading balance or SerenBucks balance is too low to execute, **immediately pause the cron job**:

```text
publisher: seren-cron, path: /jobs/{job_id}/pause, method: POST
```

Then tell the user:

> "Automated trading has been paused: insufficient funds detected. Please top up your balance before resuming the schedule."

Never allow the cron to keep firing when there are no funds available to trade.
