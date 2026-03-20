---
name: grid-trader
description: "Automated grid trading bot for Coinbase Exchange — profits from price oscillation using a mechanical, non-directional strategy"
---

# Coinbase Grid Trader

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

Automated grid trading bot for Coinbase Exchange, powered by the Seren Gateway.

## What This Skill Provides

- Automated Coinbase Exchange grid trading with dry-run and live modes
- Price-range based grid generation with risk controls
- JSONL logs for setup, orders, fills, positions, and errors
- MCP-native SerenDB persistence for sessions, events, orders, fills, and position snapshots

## What is Grid Trading?

Grid trading places a ladder of buy orders below the market price and sell orders above it. When a buy fills, a sell is placed one spacing above it. When a sell fills, a buy is placed one spacing below. Profit accumulates through price oscillation within the range — no direction prediction required.

## Setup

1. Configure Coinbase publisher credentials in Seren Desktop Settings → Publisher MCPs (desktop sidecar/keychain flow)
2. Copy `.env.example` to `.env` and set `SEREN_API_KEY` (`SEREN_DESKTOP_PUBLISHER_AUTH=true` is recommended)
3. Optional legacy fallback: set `SEREN_DESKTOP_PUBLISHER_AUTH=false` and fill `CB_ACCESS_*` values
4. Copy `config.example.json` to `config.json` and configure your grid parameters
5. Install dependencies: `pip install -r requirements.txt`
6. Run: `python scripts/agent.py`

## Trade Execution Contract

When the user says `sell`, `close`, `exit`, `unwind`, or `flatten`, treat that as an immediate operator instruction to stop new grid entries and cancel open Coinbase orders for the configured pair. If the user did not identify which pair or campaign to stop, ask only the minimum clarifying question needed to identify it.

## Pre-Trade Checklist

Before any live `start --allow-live` run:

1. Fetch current balances and the latest market price for the configured pair.
2. Verify Coinbase publisher credentials and `SEREN_API_KEY` are loaded.
3. Verify the configured grid range, quote reserve, and drawdown caps still fit the account.
4. If any credential, dependency, or market probe fails, stop here and fail closed instead of placing orders.

## Dependency Validation

Dependency validation is required before live trading. Verify `SEREN_API_KEY`, the Coinbase publisher credentials, and Python dependencies from `requirements.txt` are installed and loaded. If credentials are missing, the pair cannot be queried, or the publisher is unavailable, the runtime must stop with an error instead of submitting orders.

## Live Safety Opt-In

Default mode is dry-run. Live trading requires:

- `python scripts/agent.py start --config config.json --allow-live`
- the normal startup risk checks to pass

The `--allow-live` flag is a startup-only opt-in for that process. It is not a per-order approval prompt.

## Emergency Exit Path

To stop trading immediately, run `python scripts/agent.py stop --config config.json`. The stop path cancels all open orders for the configured pair, clears the active grid state, and leaves held spot inventory untouched until the operator chooses how to liquidate it.

## SerenDB Persistence (MCP-native)

Set these optional environment variables in `.env`:

- `SERENDB_PROJECT_NAME` (default auto target: `coinbase`)
- `SERENDB_DATABASE` (default auto target: `coinbase`)
- `SERENDB_BRANCH` (optional)
- `SERENDB_REGION` (default: `aws-us-east-1`)
- `SERENDB_AUTO_CREATE` (default: `true`)
- `SEREN_MCP_COMMAND` (default: `seren-mcp`)

Persistence is best-effort: if SerenDB/MCP is unavailable, trading still runs and logs locally.

## Configuration

See `config.example.json` for available parameters including grid spacing, order size, and trading pair selection.

## Disclaimer

This bot trades real money. Use at your own risk. Past performance does not guarantee future results.

## Seren-Cron Integration

Use `seren-cron` to run this skill on a schedule — no terminal windows to keep open, no daemons, no permanent computer changes required. Seren-cron is a cloud scheduler that calls your local trigger server on a cron schedule. Grid traders run as continuous processes; seren-cron can trigger periodic cycle checks.

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
  "name":            "coinbase-grid-trader-live",
  "url":             "http://localhost:8080/run",
  "method":          "POST",
  "cron_expression": "*/5 * * * *",
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
