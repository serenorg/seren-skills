---
name: saas-short-trader
description: "Alpaca-branded SaaS short trader that scores AI disruption risk, builds capped short baskets, tracks paper/live PnL in SerenDB, and runs continuously with seren-cron."
---

# Alpaca SaaS Short Trader

Autonomous strategy agent for shorting SaaS names under AI-driven multiple compression.  
It runs scheduled scans, intraday monitoring, post-close reconciliation, and controlled self-learning.

## What This Skill Provides

- 30-name SaaS universe scoring and ranking
- 8-name capped short basket construction
- Hedged short watchlist and catalyst notes
- Paper / paper-sim / live execution modes
- SerenDB persistence for runs, orders, marks, and PnL
- Self-learning champion/challenger loop with promotion gates
- seren-cron setup for continuous automation

## Runtime Files

- `scripts/strategy_engine.py` - core scan/monitor/post-close engine
- `scripts/serendb_storage.py` - persistence layer
- `scripts/seren_client.py` - publisher gateway client
- `scripts/self_learning.py` - learning loop
- `scripts/run_agent_server.py` - authenticated webhook runner for seren-cron
- `scripts/setup_cron.py` - create/update cron jobs
- `scripts/setup_serendb.py` - apply base + learning schemas
- `scripts/dry_run_checklist.md` - preflight + validation checklist
- `scripts/dry_run_prompt.txt` - single copy/paste dry-run prompt

## Execution Modes

- `paper` - plan and store paper orders
- `paper-sim` - simulate fills/PnL only (default)
- `live` - real broker execution path (requires explicit user approval)

## Continuous Schedule (Recommended)

- Scan: `15 8 * * 1-5` (08:15 ET)
- Monitor: `15 10-15 * * 1-5` (hourly, 10:15-15:15 ET)
- Post-close: `20 16 * * 1-5` (16:20 ET)
- Label update: `35 16 * * 1-5`
- Retrain: `30 9 * * 6`
- Promotion check: `0 7 * * 1`

## Setup

```bash
cd alpaca/saas-short-trader
python3 -m pip install -r requirements.txt
cp .env.example .env
cp config.example.json config.json
python3 scripts/setup_serendb.py --dsn "$SERENDB_DSN"
```

## Run Once

```bash
python3 scripts/strategy_engine.py --dsn "$SERENDB_DSN" --run-type scan --mode paper-sim
python3 scripts/strategy_engine.py --dsn "$SERENDB_DSN" --run-type monitor --mode paper-sim
python3 scripts/strategy_engine.py --dsn "$SERENDB_DSN" --run-type post-close --mode paper-sim
python3 scripts/self_learning.py --dsn "$SERENDB_DSN" --action full --mode paper-sim
```

## Run Continuously (seren-cron)

1. Start runner:

```bash
SERENDB_DSN="$SERENDB_DSN" SAAS_SHORT_TRADER_WEBHOOK_SECRET="$SAAS_SHORT_TRADER_WEBHOOK_SECRET" \
python3 scripts/run_agent_server.py --host 0.0.0.0 --port 8787
```

2. Create cron jobs:

```bash
python3 scripts/setup_cron.py \
  --runner-url "https://YOUR_PUBLIC_RUNNER_URL" \
  --webhook-secret "$SAAS_SHORT_TRADER_WEBHOOK_SECRET"
```

## Safety Notes

- Live trading is never auto-enabled.
- Strategy enforces max 8 names and exposure caps.
- If required data feeds fail and strict mode is enabled, run is blocked and persisted as blocked.
