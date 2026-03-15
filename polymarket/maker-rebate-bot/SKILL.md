---
name: polymarket-maker-rebate-bot
description: "Provide two-sided liquidity on Polymarket with rebate-aware quoting, inventory controls, and dry-run-first execution for binary markets."
---

# Polymarket Maker Rebate Bot

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

## When to Use

- run a fast 90-day backtest on Polymarket maker-rebate logic before trading
- market make on Polymarket with rebate-aware quoting and inventory controls
- compare paper backtest outcomes, then decide whether to run quote mode

## Workflow Summary

1. `fetch_backtest_universe` loads candidate markets from Seren Polymarket publishers (or local fixtures).
2. `replay_90d_history` runs an event-driven, stateful replay with inventory and cash carried forward.
3. `score_edge_and_pnl` estimates realized edge and PnL using order-book-aware fills plus pessimistic spread decay.
4. `summarize_backtest` returns return %, drawdown, fill telemetry path, quoted rate, and market-level results.
5. `filter_markets` removes markets near resolution or outside quality thresholds.
6. `emit_quotes` produces quote intents in `quote` mode after backtest review.
7. `live_guard` blocks live execution unless both config and explicit CLI confirmation are present.

## Execution Modes

- `backtest` (default): runs a 90-day historical replay and outputs results immediately.
- `quote`: computes current quote intents with inventory/risk guards.
- `monitor`: alias for quote-style dry monitoring output.
- `live`: requires both `execution.live_mode=true` in config and `--yes-live` CLI confirmation.

Live execution also requires:

- `POLY_PRIVATE_KEY` (or `WALLET_PRIVATE_KEY`) for EIP-712 order signing
- `POLY_API_KEY`, `POLY_PASSPHRASE`, and `POLY_SECRET` for authenticated submission

## Runtime Files

- `scripts/agent.py` - rebate-aware quoting engine with risk guards
- `config.example.json` - baseline strategy and 90-day backtest parameters
- `.env.example` - optional fallback auth/env template (`SEREN_API_KEY` only if runtime auth is unavailable)
- `requirements.txt` - installs `py-clob-client` for live order signing/submission

## Quick Start

```bash
cd ~/.config/seren/skills/polymarket-maker-rebate-bot
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env
cp config.example.json config.json
python3 scripts/agent.py --config config.json
```

This runs the default 90-day backtest and returns a decision hint to keep paper-only or proceed to quote mode.
If you are already running inside Seren Desktop, the runtime can use injected auth automatically.

> **Live market data only.** Always leave `"markets": []` and `"state": {"inventory": {}}` empty in your config.json.
> The skill fetches live markets automatically from the Polymarket API via `backtest.gamma_markets_url`.
> Never add placeholder or example market IDs (e.g. `MKT-001`) — they do not exist on Polymarket and will cause the backtest to fail with "No markets with sufficient history".

## Run Quote Mode (After Backtest Review)

```bash
python3 scripts/agent.py --config config.json --run-type quote
```

## Optional Backtest Input

By default the runtime fetches backtest data from Polymarket market/history APIs. You can also pass local history:

```bash
python3 scripts/agent.py \
  --config config.json \
  --run-type backtest \
  --backtest-file tests/fixtures/backtest_markets.json
```

Each backtest market object should include:

- `market_id` (string)
- `question` (string)
- `token_id` (string)
- `end_ts` or `endDate` (market resolution timestamp)
- `history` array of `{ "t": unix_ts, "p": probability_0_to_1 }`
- optional `orderbooks` array of `{ "t": unix_ts, "best_bid": ..., "best_ask": ..., "bid_size_usd": ..., "ask_size_usd": ... }`
- optional `rebate_bps` (number; otherwise default rebate from config)

## Seren Predictions Intelligence

After a backtest completes, the output will suggest enabling **Seren Predictions** if it is not already active. This optional feature uses cross-platform consensus and divergence signals from Kalshi, Manifold, Metaculus, PredictIt, and Betfair to:

- Boost market selection scores for markets where Polymarket diverges from consensus
- Add directional skew to quotes based on cross-platform price differences
- Filter for higher-edge opportunities where platforms disagree

To enable, set `predictions_enabled: true` in the `backtest` section of your `config.json`. Estimated cost: ~$0.30 SerenBucks per backtest run.

## Safety Notes

- Live execution is never enabled by default.
- Live quote cycles cancel stale orders, fetch fresh market snapshots, and then poll open orders/positions after requoting.
- Backtests are estimates and can materially differ from live outcomes.
- Replay enforces the same market, total, and position caps used by quote mode.
- Backtests emit JSONL quote/fill telemetry for later calibration when `backtest.telemetry_path` is set.
- Quotes are blocked when estimated edge is negative.
- Markets close to resolution are excluded.
- Position and notional caps are enforced before orders are emitted.
- This strategy can lose money during fast information updates, gaps, liquidity changes, or rebate policy changes.

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
  "name":            "polymarket-maker-rebate-bot-live",
  "url":             "http://localhost:8080/run",
  "method":          "POST",
  "cron_expression": "*/30 * * * *",
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
