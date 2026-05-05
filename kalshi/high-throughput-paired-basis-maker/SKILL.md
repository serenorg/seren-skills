---
name: high-throughput-paired-basis-maker
display-name: "Kalshi High-Throughput Paired Basis Maker"
description: "Run a paired-market basis strategy on Kalshi with mandatory backtest-first gating before trade intents."
---

# Kalshi High-Throughput Paired Basis Maker

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

## When to Use

- Trade relative-value dislocations between logically linked Kalshi contracts
- Enforce backtest-first validation before generating paired trade intents
- Run a dry-run-first workflow for hedged pair execution on Kalshi event markets

## On Invoke

**Immediately run the default paired-market backtest without asking.** Do not present a menu of modes. Execute:

```bash
cd ~/.config/seren/skills/kalshi-high-throughput-paired-basis-maker && source .venv/bin/activate && python3 scripts/agent.py --config config.json
```

Display the full backtest results to the user. Only after results are displayed, present available next steps (trade mode). If the user explicitly requests a specific mode in their invocation message, run that mode instead.

## Backtest Period

- Default: `270` days
- Allowed range: `90` to `540` days
- Why this range: basis relationships need enough time to observe repeated widening/convergence cycles, but should still emphasize current structural behavior.

## Workflow Summary

1. `_fetch_live_backtest_pairs` discovers Kalshi events via `/events` API, finds grouped markets within each event, builds correlated pairs, fetches price history for each leg via `/markets/{ticker}/history`, and attaches synthetic orderbook snapshots.
2. `simulate_pair_backtest` runs an event-driven stateful replay with carried cash and inventory across both legs, order-book-aware fills, and pessimistic spread-decay.
3. `run_backtest` aggregates results and reports total return, annualized return, Sharpe-like score, max drawdown, hit rate, fill counts, order-book mode coverage, and pair-level contributions.
4. **Sample gate** fails backtest if `events < backtest.min_events` (default `200`).
5. **Backtest gate** blocks trade mode by default if backtest return is non-positive.
6. `run_trade` outputs two-leg trade intents (`primary` + `pair`) with risk caps.

## Execution Modes

- `backtest` (default): paired historical simulation only.
- `trade`: always runs backtest first, then emits paired trade intents if gate passes.

Live execution requires all of:

- `execution.live_mode=true` in config
- `--yes-live` on the CLI
- `KALSHI_API_KEY` and `KALSHI_PRIVATE_KEY_PATH` (or `KALSHI_PRIVATE_KEY`) environment variables

## Trade Execution Contract

When the user gives a direct exit instruction (`sell`, `close`, `exit`, `unwind`, `flatten`), execute the exit path immediately.
Do not editorialize or argue against recovering remaining funds.
If the user request is ambiguous, ask only the minimum clarifying question needed to identify the positions to exit.

## Pre-Trade Checklist (Mandatory)

Before any live buy, sell, or unwind:

1. Fetch the live order book for every ticker involved via `/markets/{ticker}/orderbook`.
2. Verify prices are in cents (1-99) and compute visible-book recovery or cost across all levels.
3. Verify `KALSHI_API_KEY` and `KALSHI_PRIVATE_KEY_PATH` are loaded and the RSA signing produces valid auth headers.
4. If any dependency check fails, fail closed with a concrete remediation message.

## Emergency Exit

Immediately liquidate held inventory with:

```bash
python3 scripts/agent.py --config config.json --unwind-all --yes-live
```

The unwind path cancels open orders first, then submits market sells for all positions.

## Runtime Files

- `scripts/agent.py` - basis backtest + paired trade-intent runtime
- `scripts/kalshi_client.py` - Kalshi REST API client with RSA key signing
- `scripts/pair_stateful_replay.py` - event-driven stateful pair replay engine
- `scripts/risk_guards.py` - drawdown, position aging, and cron auto-pause guards
- `scripts/seren_client.py` - Seren publisher gateway client
- `scripts/setup_cron.py` - create/update the skill-local seren-cron local-pull runner and job
- `scripts/run_local_pull_runner.py` - poll seren-cron and execute due local jobs on this machine
- `config.example.json` - strategy parameters and backtest defaults
- `.env.example` - environment template for API credentials
- `requirements.txt` - Python dependencies

## API Key Setup

Before running this skill, check for an existing Seren API key in this order:

1. **Seren Desktop auth** -- if the skill is running inside Seren Desktop, the runtime injects `API_KEY` automatically. Check: `echo $API_KEY`. If set, no further action is needed.
2. **Existing `.env` file** -- check if `SEREN_API_KEY` is already set in the skill's `.env` file. If set, no further action is needed.
3. **Shell environment** -- check if `SEREN_API_KEY` is exported in the current shell. If set, no further action is needed.

**Only if none of the above are set**, register a new agent account:

```bash
curl -sS -X POST "https://api.serendb.com/auth/agent" \
  -H "Content-Type: application/json" \
  -d '{"name":"kalshi-high-throughput-paired-basis-maker"}'
```

Extract the API key from the response at `.data.agent.api_key` -- **this key is shown only once**. Write it to the skill's `.env` file:

```env
SEREN_API_KEY=<the-returned-key>
```

Verify:

```bash
curl -sS "https://api.serendb.com/auth/me" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

**Do not create a new account if a key already exists.** Creating a duplicate account results in a $0-balance key that overrides the user's funded account.

Reference: [https://docs.serendb.com/skills.md](https://docs.serendb.com/skills.md)

## Quick Start

```bash
cd ~/.config/seren/skills/kalshi-high-throughput-paired-basis-maker
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
> The skill discovers and fetches live Kalshi pairs automatically via the `/events` API.
> Never add placeholder market tickers -- they do not exist on Kalshi and will cause the backtest to fail.

## Run Trade Mode (Backtest-First)

```bash
python3 scripts/agent.py --config config.json --run-type trade
```

## Optional Fixture Replay

```bash
python3 scripts/agent.py --config config.json --backtest-file tests/fixtures/backtest_pairs.json
```

Set `backtest.telemetry_path` to capture JSONL replay telemetry for each decision step.

## Kalshi-Specific Notes

- **Prices in CENTS (1-99)**: All Kalshi prices are in cents. The skill normalizes internally to 0.01-0.99 decimal range.
- **Events API is key**: Basis pairs are discovered through `/events` which groups logically related markets (e.g., "Will inflation exceed 3%?" and "Will CPI beat expectations?").
- **No maker rebates**: Unlike Polymarket, Kalshi does not offer maker rebates. The edge calculation accounts for this.
- **RSA key signing**: Kalshi authentication uses RSA private key signing with `KALSHI-ACCESS-KEY`, `KALSHI-ACCESS-SIGNATURE`, and `KALSHI-ACCESS-TIMESTAMP` headers.
- **Contract mechanics**: Each Kalshi contract pays $1 if correct, $0 if wrong. Prices 1-99 cents represent the market's probability estimate.

## Disclaimer

This skill can lose money. Basis spreads can persist or widen, hedge legs can slip, and liquidity can fail during volatility. Backtests are hypothetical and do not guarantee future results. This skill is software tooling and not financial advice. Use dry-run first and only trade with risk capital.

## Seren-Cron Integration

Use the skill-local `seren-cron` local-pull runner for scheduling. The schedule lives in Seren, but a local polling process must stay online on the machine that will execute the strategy.

**Requirements:** Seren Desktop login or a valid `SEREN_API_KEY`. Live schedules also require Kalshi credentials plus funded SerenBucks.

Current Seren funding flow:

- Buy SerenBucks at `https://serendb.com/serenbucks` or `https://console.serendb.com`
- Stripe deposits start at `$5`
- A verified email is required before Stripe deposits
- API-first users can fund with `POST /wallet/deposit`

### Step 1 -- Check seren-cron is available

```text
publisher: seren-cron
path:      /api/health
method:    GET
```

### Step 2 -- Create or update the local pull schedule

Create or upsert the runner plus the local-pull job:

```bash
python3 scripts/setup_cron.py create --config config.json --schedule "*/30 * * * *"
```

For live mode, include `--yes-live` after you have set `execution.live_mode=true` in `config.json`.

### Step 3 -- Start the local pull runner

Start the polling process that claims due work and runs `scripts/agent.py` locally:

```bash
python3 scripts/run_local_pull_runner.py --config config.json
```

Leave this process running on the machine that should execute the strategy.

### Step 4 -- Manage the schedule and runner

```bash
python3 scripts/setup_cron.py list
python3 scripts/setup_cron.py list-runners
python3 scripts/setup_cron.py pause --job-id <job_id>
python3 scripts/setup_cron.py resume --job-id <job_id>
python3 scripts/setup_cron.py delete --job-id <job_id>
python3 scripts/setup_cron.py delete-runner --runner-id <runner_id>
```

Pause the job immediately if live execution fails because trading funds or SerenBucks are exhausted.
