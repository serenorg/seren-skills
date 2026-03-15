---
name: paired-market-basis-maker
description: "Run a paired-market basis strategy on Polymarket with mandatory backtest-first gating before trade intents."
---

# Paired Market Basis Maker

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

## When to Use

- trade relative-value dislocations between logically linked Polymarket contracts
- enforce backtest-first validation before generating paired trade intents
- run a dry-run-first workflow for hedged pair execution

## Backtest Period

- Default: `270` days
- Allowed range: `90` to `540` days
- Why this range: basis relationships need enough time to observe repeated widening/convergence cycles, but should still emphasize current structural behavior.

## Workflow Summary

1. `load_backtest_pairs` pulls live market histories from the Seren Polymarket Publisher (Gamma + CLOB proxied), attaches per-leg order-book snapshots, builds pairs from the active market universe, and timestamp-aligns each pair.
2. `simulate_basis_reversion` runs an event-driven stateful replay with carried cash and inventory across both legs, order-book-aware fills, and pessimistic spread-decay.
3. `summarize_backtest` reports total return, annualized return, Sharpe-like score, max drawdown, hit rate, quoted/fill counts, order-book mode coverage, telemetry counts, and pair-level contributions.
4. `sample_gate` fails backtest if `events < backtest.min_events` (default `200`).
5. `backtest_gate` blocks trade mode by default if backtest return is non-positive.
6. `emit_pair_trades` outputs two-leg trade intents (`primary` + `pair`) with risk caps.

## Execution Modes

- `backtest` (default): paired historical simulation only.
- `trade`: always runs backtest first, then emits paired trade intents if gate passes.

Live execution requires both:

- `execution.live_mode=true` in config
- `--yes-live` on the CLI
- `POLY_PRIVATE_KEY` (or `WALLET_PRIVATE_KEY`) plus `POLY_API_KEY` / `POLY_PASSPHRASE` / `POLY_SECRET`

## Runtime Files

- `scripts/agent.py` - basis backtest + paired trade-intent runtime
- `config.example.json` - strategy parameters and live backtest defaults
- `.env.example` - environment template for API credentials
- `requirements.txt` - installs `py-clob-client` for live order signing/submission

## Quick Start

```bash
cd ~/.config/seren/skills/polymarket-paired-market-basis-maker
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env
cp config.example.json config.json
python3 scripts/agent.py --config config.json
```

If you are already running inside Seren Desktop, the runtime can use injected auth automatically.

> **Live market data only.** Always leave `"markets": []` and `"state": {"leg_exposure": {}}` empty in your config.json.
> The skill discovers and fetches live Polymarket pairs automatically via `backtest.gamma_markets_url`.
> Never add placeholder market IDs — they do not exist on Polymarket and will cause the backtest to fail with "No markets with sufficient history".

## Run Trade Mode (Backtest-First)

```bash
python3 scripts/agent.py --config config.json --run-type trade
```

## Optional Fixture Replay

```bash
python3 scripts/agent.py --config config.json --backtest-file tests/fixtures/backtest_pairs.json
```

Set `backtest.telemetry_path` to capture JSONL replay telemetry for each decision step.

## Seren Predictions Intelligence

After a backtest completes, the output will suggest enabling **Seren Predictions** if it is not already active. This optional feature uses computed pair-specific endpoints to:

- Validate pair correlation strength with cross-platform data
- Rank pairs by basis deviation sigma for better entry signals
- Filter for pairs where cross-platform data confirms mean-reversion potential

Endpoints used (computed, not consensus):

- `GET /api/polymarket/pairs/suggested` ($0.10) — suggested pairs ranked by basis deviation
- `GET /api/polymarket/correlations` ($0.10) — pair correlation and basis spread statistics

To enable, set `predictions_enabled: true` in the `backtest` section of your `config.json`. Estimated cost: ~$0.20 SerenBucks per backtest run.

## Disclaimer

This skill can lose money. Basis spreads can persist or widen, hedge legs can slip, and liquidity can fail during volatility. Backtests are hypothetical and do not guarantee future results. This skill is software tooling and not financial advice. Use dry-run first and only trade with risk capital.

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
  "name":            "polymarket-pmbm-live",
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
