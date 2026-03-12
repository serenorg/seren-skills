---
name: liquidity-paired-basis-maker
description: "Run a liquidity-filtered paired-market basis strategy on Polymarket with mandatory backtest-first gating before trade intents."
---

# Liquidity Paired Basis Maker

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

## When to Use

- trade relative-value dislocations between logically linked Polymarket contracts
- enforce backtest-first validation before generating paired trade intents
- run a dry-run-first workflow for hedged pair execution

## Backtest Period

- Default: `90` days
- Allowed range: `90` to `365` days
- Why this range: keeps the replay current while requiring enough data for repeated widening/convergence behavior.

## Workflow Summary

1. `load_backtest_pairs` pulls live market histories from Seren Polymarket Publisher (Gamma markets + CLOB history), attaches per-leg order-book snapshots, applies a liquidity-filtered universe cap, builds pairs, and timestamp-aligns each pair.
2. `simulate_basis_reversion` runs an event-driven stateful replay with carried cash and inventory across both legs, order-book-aware fills, and pessimistic spread-decay.
3. `summarize_backtest` reports total return, annualized return, Sharpe-like score, max drawdown, hit rate, quoted/fill counts, order-book mode coverage, telemetry counts, and pair-level contributions.
4. `sample_gate` fails backtest if `events < backtest.min_events` (default `120`).
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
- `config.example.json` - strategy parameters, live backtest defaults, and trade-mode sample markets
- `.env.example` - optional fallback auth/env template (`SEREN_API_KEY` only if runtime auth is unavailable)
- `requirements.txt` - installs `py-clob-client` for live order signing/submission

## Quick Start

```bash
cd ~/.config/seren/skills/polymarket-liquidity-paired-basis-maker
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env
cp config.example.json config.json
python3 scripts/agent.py --config config.json
```

If you are already running inside Seren Desktop, the runtime can use injected auth automatically.

## Run Trade Mode (Backtest-First)

```bash
python3 scripts/agent.py --config config.json --run-type trade
```

## Optional Fixture Replay

```bash
python3 scripts/agent.py --config config.json --backtest-file tests/fixtures/backtest_pairs.json
```

Set `backtest.telemetry_path` to capture JSONL replay telemetry for each decision step.

## Disclaimer

This skill can lose money. Basis spreads can persist or widen, hedge legs can slip, and liquidity can fail during volatility. Backtests are hypothetical and do not guarantee future results. This skill is software tooling and not financial advice. Use dry-run first and only trade with risk capital.
