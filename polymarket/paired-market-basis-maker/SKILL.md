---
name: paired-market-basis-maker
description: "Run a paired-market basis strategy on Polymarket with mandatory backtest-first gating before trade intents."
---

# Paired Market Basis Maker

## When to Use

- trade relative-value dislocations between logically linked Polymarket contracts
- enforce backtest-first validation before generating paired trade intents
- run a dry-run-first workflow for hedged pair execution

## Backtest Period

- Default: `270` days
- Allowed range: `90` to `540` days
- Why this range: basis relationships need enough time to observe repeated widening/convergence cycles, but should still emphasize current structural behavior.

## Workflow Summary

1. `load_backtest_pairs` ingests paired histories (`history` + `pair_history`).
2. `simulate_basis_reversion` evaluates entry/exit behavior on basis widening and convergence.
3. `summarize_backtest` reports return %, PnL, drawdown, trade-rate, and pair-level contributions.
4. `backtest_gate` blocks trade mode by default if backtest return is non-positive.
5. `emit_pair_trades` outputs two-leg trade intents (`primary` + `pair`) with risk caps.

## Execution Modes

- `backtest` (default): paired historical simulation only.
- `trade`: always runs backtest first, then emits paired trade intents if gate passes.

Live execution requires both:

- `execution.live_mode=true` in config
- `--yes-live` on the CLI

## Runtime Files

- `scripts/agent.py` - basis backtest + paired trade-intent runtime
- `config.example.json` - strategy parameters and paired sample markets
- `.env.example` - environment template for API credentials

## Quick Start

```bash
cd polymarket/paired-market-basis-maker
cp .env.example .env
cp config.example.json config.json
python3 scripts/agent.py --config config.json
```

## Run Trade Mode (Backtest-First)

```bash
python3 scripts/agent.py --config config.json --run-type trade
```

## Disclaimer

This skill can lose money. Basis spreads can persist or widen, hedge legs can slip, and liquidity can fail during volatility. Backtests are hypothetical and do not guarantee future results. This skill is software tooling and not financial advice. Use dry-run first and only trade with risk capital.
