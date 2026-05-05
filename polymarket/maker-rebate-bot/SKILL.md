---
name: maker-rebate-bot
description: "Provide two-sided liquidity on Polymarket with rebate-aware quoting, inventory controls, and dry-run-first execution for binary markets."
---
# Polymarket Maker Rebate Bot

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

## When to Use

- run a fast 90-day backtest on Polymarket maker-rebate logic before trading
- market make on Polymarket with rebate-aware quoting and inventory controls
- compare paper backtest outcomes, then decide whether to run quote mode

## On Invoke

**Immediately run the default 90-day backtest without asking.** Do not present a menu of modes. Execute:

```bash
cd ~/.config/seren/skills/polymarket-maker-rebate-bot && source .venv/bin/activate && python3 scripts/agent.py --config config.json
```

Display the full backtest results to the user. Only after results are displayed, present available next steps (quote mode, unwind, monitor). If the user explicitly requests a specific mode in their invocation message, run that mode instead.

## Workflow Summary

1. `fetch_backtest_universe` loads candidate markets from Seren Polymarket publishers (or local fixtures).
2. `replay_90d_history` runs an event-driven, stateful replay with inventory and cash carried forward.
3. `score_edge_and_pnl` estimates realized edge and PnL using order-book-aware fills plus pessimistic spread decay.
4. `summarize_backtest` returns return %, drawdown, fill telemetry path, quoted rate, and market-level results.
5. `filter_markets` removes markets outside the default safe band (`$0.30-$0.70` midpoint), below the default daily-volume floor (`$5,000`), or inside the default resolution buffer (`14` days).
6. `inventory_guard` tracks held inventory across cycles, forces `sell_only` behavior on policy-breaching markets, and escalates to a marketable unwind after the default `3`-cycle hold limit or when inventory drifts outside the safe midpoint band.
7. `emit_quotes` produces quote intents in `quote` mode after backtest review.
8. `live_guard` blocks live execution unless both config and explicit CLI confirmation are present.

## Execution Modes

- `backtest` (default): runs a 90-day historical replay and outputs results immediately.
- `quote`: computes current quote intents with inventory/risk guards.
- `monitor`: alias for quote-style dry monitoring output.
- `live`: requires both `execution.live_mode=true` in config and `--yes-live` CLI confirmation.

Live execution also requires:

- `POLY_PRIVATE_KEY` (or `WALLET_PRIVATE_KEY`) for EIP-712 order signing
- `POLY_API_KEY`, `POLY_PASSPHRASE`, and `POLY_SECRET` for authenticated submission

## Trade Execution Contract

When the user gives a direct exit instruction (`sell`, `close`, `exit`, `unwind`, `flatten`), execute the exit path immediately.
Do not editorialize or argue against recovering remaining funds.
If the user request is ambiguous, ask only the minimum clarifying question needed to identify the positions to exit.

## CLOB Exit Rules

- `py-clob-client` via `DirectClobTrader` is the canonical live execution path.
- For immediate sells, use a marketable limit priced at the market minimum tick from the live order book. Do not hardcode `$0.001`; use the current `tick_size`.
- Never place a passive sell above the best bid when the user asked for an immediate exit.
- Estimate recovery by sweeping visible bid levels (`price x size`) across the full book, not just the best bid.
- If visible bid depth cannot cover the full exit size, report the partial-depth estimate and remaining unfilled size.

## Pre-Trade Checklist (Mandatory)

Before any live buy, sell, or unwind:

1. Fetch the live order book for every token involved.
2. Snap prices to the market `tick_size` and compute visible-book recovery or cost across all levels.
3. Verify the current environment can legally and technically reach the Polymarket CLOB API. If access is blocked, stop and report the restriction; do not suggest bypasses.
4. Verify `py-clob-client` is installed and `POLY_PRIVATE_KEY` or `WALLET_PRIVATE_KEY`, `POLY_API_KEY`, `POLY_PASSPHRASE`, and `POLY_SECRET` are loaded.
5. If any dependency check fails, fail closed with a concrete remediation message.

## Emergency Exit

Immediately liquidate held inventory with:

```bash
python3 scripts/agent.py --config config.json --unwind-all --yes-live
```

The unwind path cancels open orders first, then submits marketable min-tick sells and reports visible-book exit estimates.

## Runtime Files

- `scripts/agent.py` - rebate-aware quoting engine with risk guards
- `scripts/setup_cron.py` - create/update the skill-local seren-cron local-pull runner and job
- `scripts/run_local_pull_runner.py` - poll seren-cron and execute due local jobs on this machine
- `config.example.json` - baseline strategy and 90-day backtest parameters
- `.env.example` - optional fallback auth/env template (`SEREN_API_KEY` only if runtime auth is unavailable)
- `requirements.txt` - installs `py-clob-client` for live order signing/submission

## API Key Setup

Before running this skill, check for an existing Seren API key in this order:

1. **Seren Desktop auth** — if the skill is running inside Seren Desktop, the runtime injects `API_KEY` automatically. Check: `echo $API_KEY`. If set, no further action is needed.
2. **Existing `.env` file** — check if `SEREN_API_KEY` is already set in the skill's `.env` file. If set, no further action is needed.
3. **Shell environment** — check if `SEREN_API_KEY` is exported in the current shell. If set, no further action is needed.

**Only if none of the above are set**, register a new agent account:

```bash
curl -sS -X POST "https://api.serendb.com/auth/agent" \
  -H "Content-Type: application/json" \
  -d '{"name":"polymarket-maker-rebate-bot"}'
```

Extract the API key from the response at `.data.agent.api_key` — **this key is shown only once**. Write it to the skill's `.env` file:

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
> Leave `"state.inventory_cycles": {}` empty as well. The runtime increments hold-cycle state from live positions and uses it to trigger inventory unwinds.

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
- Replay now blocks new exposure outside the default `0.30-0.70` midpoint band, below the default `$5,000` 24-hour volume floor, and inside the default `14`-day resolution buffer.
- Held inventory is not allowed to drift indefinitely. The runtime persists hold cycles, switches policy-breaching inventory to `sell_only`, and forces a marketable unwind once the configured hold limit is reached or the midpoint drifts outside the safe band.
- Backtests emit JSONL quote/fill telemetry for later calibration when `backtest.telemetry_path` is set.
- Quotes are blocked when estimated edge is negative.
- New entries close to resolution are excluded.
- Position and notional caps are enforced before orders are emitted.
- This strategy can lose money during fast information updates, gaps, liquidity changes, or rebate policy changes.

## Seren-Cron Integration

Use the skill-local `seren-cron` local-pull runner for scheduling. The schedule lives in Seren, but a local polling process must stay online on the machine that will execute the strategy.

**Requirements:** Seren Desktop login or a valid `SEREN_API_KEY`. Live schedules also require Polymarket credentials plus funded SerenBucks.

Current Seren funding flow:

- Buy SerenBucks at `https://serendb.com/serenbucks` or `https://console.serendb.com`
- Stripe deposits start at `$5`
- A verified email is required before Stripe deposits
- API-first users can fund with `POST /wallet/deposit`

### Step 1 — Check seren-cron is available

```text
publisher: seren-cron
path:      /api/health
method:    GET
```

### Step 2 — Create or update the local pull schedule

Create or upsert the runner plus the local-pull job:

```bash
python3 scripts/setup_cron.py create --config config.json --schedule "*/30 * * * *"
```

For live mode, include `--yes-live` after you have set `execution.live_mode=true` in `config.json`.

### Step 3 — Start the local pull runner

Start the polling process that claims due work and runs `scripts/agent.py` locally:

```bash
python3 scripts/run_local_pull_runner.py --config config.json
```

Leave this process running on the machine that should execute the strategy.

### Step 4 — Manage the schedule and runner

```bash
python3 scripts/setup_cron.py list
python3 scripts/setup_cron.py list-runners
python3 scripts/setup_cron.py pause --job-id <job_id>
python3 scripts/setup_cron.py resume --job-id <job_id>
python3 scripts/setup_cron.py delete --job-id <job_id>
python3 scripts/setup_cron.py delete-runner --runner-id <runner_id>
```

Pause the job immediately if live execution fails because trading funds or SerenBucks are exhausted.
