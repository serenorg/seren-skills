---
name: grid-trader
description: "Automated grid trading bot for Kraken — profits from BTC volatility using a mechanical, non-directional strategy"
---

# Kraken Grid Trader

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

Automated grid trading bot for Kraken that profits from BTC volatility using a mechanical, non-directional strategy.

## On Invoke

**Immediately run a dry-run grid simulation without asking.** Do not present a menu of modes. Execute:

```bash
cd ~/.config/seren/skills/grid-trader && source .venv/bin/activate && python3 scripts/agent.py dry-run --config config.json --cycles 5
```

Display the full dry-run results to the user. Only after results are displayed, present available next steps (live mode with `--allow-live`). If the user explicitly requests a specific mode in their invocation message, run that mode instead.

## What This Skill Provides

- Automated Kraken grid trading with dry-run and live modes
- Pair selection support (single pair or candidate list)
- Adaptive grid centering, spacing/order-size tuning, and persistent learning state
- Shadow evaluation with gated promotion and rollback
- JSONL logs for setup, orders, fills, positions, and errors
- MCP-native SerenDB persistence for sessions, events, orders, fills, position snapshots, adaptive runtime state, telemetry, reviews, and runtime locks

## What is Grid Trading?

Grid trading places buy and sell orders at regular price intervals (the "grid"). When price moves up and down, orders fill automatically — accumulating profit from oscillation without predicting direction.

## Setup

1. Configure Kraken publisher credentials in Seren Desktop Settings → Publisher MCPs (desktop sidecar/keychain flow)
2. Copy `.env.example` to `.env` and set `SEREN_API_KEY`
3. Copy `config.example.json` to `config.json` and configure your grid parameters
4. Install dependencies: `pip install -r requirements.txt`
5. Run: `python scripts/agent.py`

## Trade Execution Contract

When the user says `sell`, `close`, `exit`, `unwind`, or `flatten`, treat that as an immediate operator instruction to stop new grid entries and cancel open Kraken orders for the configured pair. If the user did not identify which pair or campaign to stop, ask only the minimum clarifying question needed to identify it.

## Pre-Trade Checklist

Before any live `start --allow-live` run:

1. Fetch balances and the latest Kraken price for the active pair.
2. Verify `SEREN_API_KEY` and Kraken publisher credentials are loaded.
3. Verify grid spacing, quote reserve, position size, and drawdown caps still fit the account.
4. If any credential, dependency, or market probe fails, stop here and fail closed instead of placing orders.

## Dependency Validation

Dependency validation is required before live trading. Verify `SEREN_API_KEY`, Kraken publisher credentials, and Python dependencies from `requirements.txt` are installed and loaded. If credentials are missing, the pair cannot be queried, or the publisher is unavailable, the runtime must stop with an error instead of submitting orders.

## Live Safety Opt-In

Default mode is dry-run. Live trading requires:

- `python scripts/agent.py start --config config.json --allow-live`
- or `python scripts/agent.py cycle --config config.json --allow-live`
- the normal startup risk checks to pass

The `--allow-live` flag is a startup-only opt-in for that process. It is not a per-order approval prompt.

## Emergency Exit Path

To stop trading immediately, run `python scripts/agent.py stop --config config.json`. The stop path cancels all open orders for the configured pair, clears the active grid state, and leaves held spot inventory untouched until the operator chooses how to liquidate it.

## SerenDB Persistence (MCP-native)

Set these optional environment variables in `.env`:

- `SERENDB_PROJECT_NAME` (default auto target: `krakent`)
- `SERENDB_DATABASE` (default auto target: `krakent`)
- `SERENDB_BRANCH` (optional)
- `SERENDB_REGION` (default: `aws-us-east-1`)
- `SERENDB_AUTO_CREATE` (default: `true`)
- `SEREN_MCP_COMMAND` (default: `seren-mcp`)

Adaptive mode requires SerenDB/MCP. If the persistence layer is unavailable, the runtime fails closed rather than falling back to local adaptive state files, runtime lock files, or local review/alert telemetry files.

## Configuration

See `config.example.json` for available parameters including grid spacing, order size, trading pair selection, daily loss caps, cooldowns, shadow thresholds, and adaptive lock lease settings.

## Disclaimer

This bot trades real money. Use at your own risk. Past performance does not guarantee future results.

## Seren-Cron Integration

Use `seren-cron` to run this skill on a schedule. The preferred automation path is one-shot scheduling: a fast adaptive `cycle`, a `safety-check`, and a weekly `review`. All three share a runtime lock so overlapping cron invocations fail closed instead of double-submitting orders.

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
SEREN_API_KEY="$SEREN_API_KEY" KRAKEN_GRID_WEBHOOK_SECRET="$KRAKEN_GRID_WEBHOOK_SECRET" \
python3 scripts/run_agent_server.py --config config.json --port 8080
```

This process runs in your terminal session. When you close the terminal, it stops — **that is expected and correct**. Seren-cron handles the scheduling; your local server handles execution.

### Step 4 — Create the cron schedule

With the server running, create the scheduled job:

```bash
python3 scripts/setup_cron.py create \
  --runner-url "https://YOUR_PUBLIC_RUNNER_URL" \
  --webhook-secret "$KRAKEN_GRID_WEBHOOK_SECRET"
```

This creates or updates:

- `kraken-grid-trader-cycle`
- `kraken-grid-trader-safety-check`
- `kraken-grid-trader-weekly-review`

### Step 5 — Manage the schedule

**List all active jobs:**

```bash
python3 scripts/setup_cron.py list
```

**Pause:**

```bash
python3 scripts/setup_cron.py pause --job-id <job_id>
```

**Resume:**

```bash
python3 scripts/setup_cron.py resume --job-id <job_id>
```

**Stop permanently:**

```bash
python3 scripts/setup_cron.py delete --job-id <job_id>
```

### Insufficient Funds Guard

If a live trade or cycle fails because the trading balance or SerenBucks balance is too low to execute, **immediately pause the cron job**:

```text
publisher: seren-cron, path: /jobs/{job_id}/pause, method: POST
```

Then tell the user:

> "Automated trading has been paused: insufficient funds detected. Please top up your balance before resuming the schedule."

Never allow the cron to keep firing when there are no funds available to trade.
