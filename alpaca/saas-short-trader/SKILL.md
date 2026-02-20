---
name: saas-short-trader
description: "Alpaca-branded SaaS short trader with MCP-native execution: scores AI disruption risk, builds capped short baskets, and tracks paper/live PnL in SerenDB."
---

# Alpaca SaaS Short Trader

Autonomous strategy agent for shorting SaaS names under AI-driven multiple compression.

Default backend is MCP-native:
- Data collection via `mcp__seren-mcp__call_publisher`
- Storage and PnL via `mcp__seren-mcp__run_sql` / `mcp__seren-mcp__run_sql_transaction`
- Project/database lifecycle via `mcp__seren-mcp__list_*` / `create_*`

Legacy Python/API scripts remain available as fallback, not default.

## What This Skill Provides

- MCP-native 30-name SaaS universe scoring and ranking
- MCP-native 8-name capped short basket construction
- Hedged short watchlist and catalyst notes
- Paper / paper-sim / live execution modes
- SerenDB persistence for runs, orders, marks, and PnL
- Self-learning champion/challenger loop with promotion gates
- seren-cron setup for continuous automation

## Runtime Files

- `scripts/dry_run_prompt.txt` - single copy/paste MCP-native run prompt (default)
- `scripts/dry_run_checklist.md` - MCP-native readiness checklist
- `scripts/mcp_native_runbook.md` - canonical MCP execution contract
- `scripts/strategy_engine.py` - core scan/monitor/post-close engine
- `scripts/serendb_storage.py` - persistence layer
- `scripts/seren_client.py` - publisher gateway client
- `scripts/self_learning.py` - learning loop
- `scripts/run_agent_server.py` - authenticated webhook runner for seren-cron
- `scripts/setup_cron.py` - create/update cron jobs
- `scripts/setup_serendb.py` - apply base + learning schemas
## Execution Modes

- `paper` - plan and store paper orders
- `paper-sim` - simulate fills/PnL only (default)
- `live` - real broker execution path (requires explicit user approval)

## MCP-Native Workflow (Default)

1. Resolve target database with MCP:
   - project: `alpaca-short-trader`
   - database: `alpaca_short_bot`
2. Ensure `serendb_schema.sql` and `self_learning_schema.sql` are applied via MCP SQL.
3. Query publishers via MCP:
   - `alpaca`
   - `sec-filings-intelligence`
   - `google-trends`
   - `perplexity` (fallback: `exa`)
4. Score exactly 30 names and cap planned shorts at 8.
5. Persist run, candidates, order events, position marks, and daily PnL to SerenDB.
6. Persist learning snapshots, labels, policy assignment/events.
7. Return selected names, feed status, and PnL summary.

Use `scripts/dry_run_prompt.txt` for one-copy/paste execution.

## Continuous Schedule (Recommended ET)

- Scan: `15 8 * * 1-5` (08:15 ET)
- Monitor: `15 10-15 * * 1-5` (hourly, 10:15-15:15 ET)
- Post-close: `20 16 * * 1-5` (16:20 ET)
- Label update: `35 16 * * 1-5` (MCP SQL upsert)
- Retrain: `30 9 * * 6`
- Promotion check: `0 7 * * 1`

## Legacy Python Fallback (Optional)

```bash
cd alpaca/saas-short-trader
python3 -m pip install -r requirements.txt
cp .env.example .env
cp config.example.json config.json
python3 scripts/setup_serendb.py --api-key "$SEREN_API_KEY"
```

## Legacy Run Once (Optional)

```bash
python3 scripts/strategy_engine.py --api-key "$SEREN_API_KEY" --run-type scan --mode paper-sim
python3 scripts/strategy_engine.py --api-key "$SEREN_API_KEY" --run-type monitor --mode paper-sim
python3 scripts/strategy_engine.py --api-key "$SEREN_API_KEY" --run-type post-close --mode paper-sim
python3 scripts/self_learning.py --api-key "$SEREN_API_KEY" --action full --mode paper-sim
```

## Legacy Continuous Runner (Optional, seren-cron)

1. Start runner:

```bash
SEREN_API_KEY="$SEREN_API_KEY" SAAS_SHORT_TRADER_WEBHOOK_SECRET="$SAAS_SHORT_TRADER_WEBHOOK_SECRET" \
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
- Prefer MCP-native execution in constrained/runtime-sandboxed environments.
