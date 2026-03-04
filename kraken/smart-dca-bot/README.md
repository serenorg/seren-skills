# Kraken Smart DCA Bot

AI-optimized DCA skill for Kraken with three modes:
- `single_asset`
- `portfolio`
- `scanner`

Trading is local-direct to Kraken REST API.

## Quick Start

1. `cp .env.example .env`
2. `cp config.example.json config.json`
3. `pip install -r requirements.txt`
4. Dry run:
   - `python scripts/agent.py --config config.json --accept-risk-disclaimer`
5. Live run:
   - set `"dry_run": false` in `config.json`
   - `python scripts/agent.py --config config.json --allow-live --accept-risk-disclaimer`

## Key Features

- Five execution strategies for Mode 1: `vwap_optimized`, `momentum_dip`, `spread_optimized`, `time_weighted`, `simple`
- Portfolio DCA with allocation drift detection and thresholded re-weighting
- Opportunity scanner with signals:
  - `oversold_rsi`
  - `volume_spike`
  - `mean_reversion`
  - `new_listing`
  - scanner allocations default to `portfolio.allocations` unless `scanner.base_allocations` is provided
  - scanner approval actions: `pending` (default), `approve`, `modify`, `skip`
- Seren API key auto-registration (`SEREN_API_KEY`) on first run
- Optional SerenDB persistence (`SERENDB_URL`)
- JSONL audit logs in `logs/`
- Cost-basis lots in `state/cost_basis_lots.json`
- Cron/webhook operations via:
  - `scripts/run_agent_server.py`
  - `scripts/setup_cron.py`
  - `scripts/setup_serendb.py`

## Scheduling

Start local trigger server:

```bash
python scripts/run_agent_server.py --config config.json --host 127.0.0.1 --port 8787 --webhook-secret "$DCA_WEBHOOK_SECRET"
```

Create seren-cron jobs:

```bash
python scripts/setup_cron.py create --url "http://localhost:8787/run" --schedule "*/15 * * * *" --name "kraken-smart-dca-bot"
```

List / pause / resume / delete jobs:

```bash
python scripts/setup_cron.py list
python scripts/setup_cron.py pause --job-id "<job_id>"
python scripts/setup_cron.py resume --job-id "<job_id>"
python scripts/setup_cron.py delete --job-id "<job_id>"
```

## Tests

```bash
pytest -q
```
