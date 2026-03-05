---
name: news-shock-guard-maker
description: "Run a Polymarket maker strategy that backtests first and suppresses quoting during news-shock regimes."
---

# News Shock Guard Maker

## When to Use

- run maker-style Polymarket quoting with explicit suppression during breaking-news volatility
- require a backtest result before generating any trade intents
- enforce dry-run-first controls with optional guarded live mode

## Backtest Period

- Default: `365` days
- Allowed range: `120` to `730` days
- Why this range: shock filtering needs multiple event cycles and macro regimes to evaluate whether volatility guards reduce drawdowns without removing too much edge.

## Workflow Summary

1. `load_backtest_markets` ingests historical price paths from config or `--backtest-file`.
2. `simulate_with_shock_guard` applies volatility-based quote logic with shock/cooldown suppression.
3. `summarize_backtest` reports return %, PnL, drawdown, quote-rate, and shock-skip counts.
4. `backtest_gate` blocks trade mode by default when backtest return is non-positive.
5. `quote_trade_intents` emits quote intents only for markets passing the shock guard.

## Execution Modes

- `backtest` (default): simulation only.
- `trade`: always runs backtest first, then emits quote intents if gating passes.

Live execution requires both:

- `execution.live_mode=true` in config
- `--yes-live` on the CLI

## Runtime Files

- `scripts/agent.py` - shock-aware backtest and trade-intent runtime
- `config.example.json` - baseline parameters and sample markets
- `.env.example` - environment template for API credentials

## Quick Start

```bash
cd polymarket/news-shock-guard-maker
cp .env.example .env
cp config.example.json config.json
python3 scripts/agent.py --config config.json
```

## Run Trade Mode (Backtest-First)

```bash
python3 scripts/agent.py --config config.json --run-type trade
```

## Disclaimer

This skill can lose money. News-driven markets can gap and invalidate historical assumptions quickly. Backtests are hypothetical and do not guarantee future results. This skill is software tooling and not financial advice. Use dry-run first, apply strict risk limits, and only trade risk capital.
