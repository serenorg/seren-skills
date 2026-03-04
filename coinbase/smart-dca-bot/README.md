# Coinbase Smart DCA Bot

AI-optimized DCA skill for Coinbase Advanced Trade with three modes:
- `single_asset`
- `portfolio`
- `opportunity_scanner`

Trading is local-direct to Coinbase. Seren is used for API key bootstrap, analytics, and optional SerenDB persistence.

## Quick Start

1. `cp .env.example .env`
2. `cp config.example.json config.json`
3. `pip install -r requirements.txt`
4. Dry run:
   - `python scripts/agent.py --config config.json --accept-risk-disclaimer`
5. Live run:
   - set `"dry_run": false` in `config.json`
   - `python scripts/agent.py --config config.json --allow-live --accept-risk-disclaimer`

## Coinbase-Specific Features

- USDC route optimizer (`inputs.use_usdc_routing`) to compare `ASSET-USD` vs `ASSET-USDC`
- Portfolio DCA with drift correction and per-asset routing
- Opportunity scanner signals:
  - `oversold_rsi`
  - `volume_spike`
  - `mean_reversion`
  - `new_listing`
  - `learn_earn`
- Scanner quality filters:
  - `scanner.min_24h_volume_usd`
  - `scanner.min_market_cap_usd`
  - `scanner.require_coinbase_verified`
- Optional staking context via `COINBASE_STAKING_APY_JSON` and post-buy staking hints
- Optional Learn rewards context via `COINBASE_LEARN_REWARDS_JSON`

## Safety / QA Guards

- Dry-run default with explicit `--allow-live`
- First-run disclaimer acknowledgment gate (`--accept-risk-disclaimer`)
- Per-trade custody warning in execution output
- Atomic daily plan-cap check before multi-order execution
- Pending status preserved for live limit orders
- Allocation sum validation (must total `1.0` or `100.0`)
- Stable scanner signal IDs (`sha256`-based)

## Scheduling

Start trigger server:

```bash
python scripts/run_agent_server.py --config config.json --host 127.0.0.1 --port 8787 --webhook-secret "$DCA_WEBHOOK_SECRET"
```

Create seren-cron job:

```bash
python scripts/setup_cron.py create --url "http://localhost:8787/run" --schedule "*/15 * * * *" --name "coinbase-smart-dca-bot"
```

## Tests

```bash
pytest -q
```
