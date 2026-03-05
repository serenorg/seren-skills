---
name: resolution-decay-maker
description: "Run a resolution-decay Polymarket maker strategy with mandatory backtest-first gating before trade mode."
---

# Resolution Decay Maker

## When to Use

- run a resolution-focused market-making strategy that sizes exposure as markets approach settlement
- require a historical backtest result before any trade-mode quote generation
- compare paper results first, then decide if guarded trade mode is acceptable

## Backtest Period

- Default: `180` days
- Allowed range: `60` to `365` days
- Why this range: resolution-decay behavior is driven by late-cycle microstructure, so the window should be long enough to include multiple market lifecycles but short enough to avoid stale regime assumptions.

## Workflow Summary

1. `load_backtest_markets` loads historical market paths from config or `--backtest-file`.
2. `simulate_resolution_decay` replays fills with decay-aware edge assumptions.
3. `summarize_backtest` outputs return %, PnL, drawdown, quote-rate, and market-level contributions.
4. `backtest_gate` blocks trade mode when `execution.require_positive_backtest=true` and backtest return is non-positive.
5. `quote_trade_intents` emits dry-run quote intents (or guarded live mode with explicit confirmation).

## Execution Modes

- `backtest` (default): run historical simulation only.
- `trade`: always runs backtest first, then emits quote intents if gate passes.

Live execution requires both:

- `execution.live_mode=true` in config
- `--yes-live` CLI confirmation

## Runtime Files

- `scripts/agent.py` - backtest engine + trade intent generator
- `config.example.json` - baseline parameters, sample markets, and backtest range
- `.env.example` - environment template for future credential wiring

## Quick Start

```bash
cd polymarket/resolution-decay-maker
cp .env.example .env
cp config.example.json config.json
python3 scripts/agent.py --config config.json
```

This command always runs the backtest first and prints historical return metrics.

## Run Trade Mode (Backtest-First)

```bash
python3 scripts/agent.py --config config.json --run-type trade
```

If backtest return is non-positive and `require_positive_backtest` is enabled, trade mode is blocked.

## Override Backtest Gate (Explicit)

```bash
python3 scripts/agent.py --config config.json --run-type trade --allow-negative-backtest
```

## Disclaimer

This skill can lose money. Backtests are hypothetical and rely on assumptions about fills, edge decay, and execution costs. Historical performance does not guarantee future results. This skill is software tooling, not financial advice. Use dry-run first and only trade with capital you can afford to lose.
