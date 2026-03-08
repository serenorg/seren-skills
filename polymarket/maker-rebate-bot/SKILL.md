---
name: polymarket-maker-rebate-bot
description: "Provide two-sided liquidity on Polymarket with rebate-aware quoting, inventory controls, and dry-run-first execution for binary markets."
---

# Polymarket Maker Rebate Bot

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
cd polymarket/maker-rebate-bot
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
