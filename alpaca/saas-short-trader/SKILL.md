---
name: saas-short-trader
description: "Alpaca-branded SaaS short trader with MCP-native execution: scores AI disruption risk, builds capped short baskets, and tracks paper/live PnL in SerenDB."
---

# Alpaca SaaS Short Trader

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

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

## On Invoke

**Immediately run a paper-sim scan without asking.** Do not present a menu of modes. Follow the MCP-native workflow in `scripts/dry_run_prompt.txt` to execute a paper-sim run. Display the full scan and scoring results to the user. Only after results are displayed, present available next steps (paper mode, live mode). If the user explicitly requests a specific mode in their invocation message, run that mode instead.

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

## Pre-Trade Checklist

Before any live run:

1. Verify `SEREN_API_KEY` is loaded and the `alpaca` publisher can read `/v2/account`.
2. Verify `sec-filings-intelligence`, `google-trends`, and `perplexity` or `exa` are reachable.
3. Verify `strict_required_feeds` and `live_controls` still fit the account before submitting orders.
4. If any required feed, credential, or account preflight fails, stop here and fail closed instead of placing orders.

## Dependency Validation

Dependency validation is required before live trading. Verify `SEREN_API_KEY`, the `alpaca` publisher, `sec-filings-intelligence`, `google-trends`, and the news research publisher are loaded and reachable. If credentials are missing, a required feed is blocked, or Alpaca account preflight fails, the runtime must stop with an error instead of submitting orders.

## Live Safety Opt-In

Default mode is `paper-sim`. Live trading requires both:

- `mode=live` in config or request payload
- `python3 scripts/strategy_engine.py --config config.json --mode live --allow-live ...`

For scheduled execution, the trigger server must be started with `--allow-live` or the webhook payload must set `allow_live=true`. This is a startup-only live opt-in for that process or schedule, not a per-order approval prompt.

## Emergency Exit Path

To stop trading immediately, run `python3 scripts/strategy_engine.py --config config.json --stop-trading` or send `action=stop-trading` to the webhook runner. The stop-trading path cancels all tracked live orders for the latest strategy run without requiring an extra live confirmation.

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

## Seren-Cron Integration

Use `seren-cron` to run this skill on a schedule — no terminal windows to keep open, no daemons, no permanent computer changes required. Seren-cron is a cloud scheduler that calls your local trigger server on a cron schedule.

**Requirements:** Seren Desktop login or a valid `SEREN_API_KEY`.

### Step 1 — Check seren-cron is available

Before scheduling, verify the publisher is reachable using `mcp__seren__call_publisher`:

```text
publisher: seren-cron
path:      /health
method:    GET
```

If this call fails, **stop here** and tell the user:

> "The seren-cron service could not be reached. Please send this error to <hello@serendb.com> for support."

### Step 2 — Review active cron jobs (always do this first)

**Always check for existing scheduled jobs before creating a new one.** A user may have forgotten a live job is already running.

```text
publisher: seren-cron
path:      /jobs
method:    GET
```

If jobs for this skill already exist, show them to the user and ask:

> "You have [N] active cron job(s) for this skill. Would you like to:
>
> 1. Keep them running (recommended if intentional)
> 2. Stop all and create a new schedule
> 3. Cancel"

**Do not create a duplicate cron job without explicit user confirmation.**

### Step 3 — Start the local trigger server

Start the webhook server that seren-cron will call on each scheduled tick:

```bash
SEREN_API_KEY="$SEREN_API_KEY" python3 scripts/run_agent_server.py --config config.json --port 8080
```

This process runs in your terminal session. When you close the terminal, it stops — **that is expected and correct**. Seren-cron handles the scheduling; your local server handles execution.

### Step 4 — Create the cron schedules

This skill uses multiple schedules aligned to market hours (ET). Create one job per schedule:

**Scan (08:15 ET weekdays):**

```text
publisher: seren-cron
path:      /jobs
method:    POST
body: {
  "name":            "alpaca-saas-short-trader-live-scan",
  "url":             "http://localhost:8080/run",
  "method":          "POST",
  "cron_expression": "15 8 * * 1-5",
  "timezone":        "UTC",
  "enabled":         true,
  "timeout_seconds": 60
}
```

**Monitor (10:15–15:15 ET weekdays, hourly):**

```text
publisher: seren-cron
path:      /jobs
method:    POST
body: {
  "name":            "alpaca-saas-short-trader-live-monitor",
  "url":             "http://localhost:8080/run",
  "method":          "POST",
  "cron_expression": "15 10-15 * * 1-5",
  "timezone":        "UTC",
  "enabled":         true,
  "timeout_seconds": 60
}
```

**Post-close (16:20 ET weekdays):**

```text
publisher: seren-cron
path:      /jobs
method:    POST
body: {
  "name":            "alpaca-saas-short-trader-live-postclose",
  "url":             "http://localhost:8080/run",
  "method":          "POST",
  "cron_expression": "20 16 * * 1-5",
  "timezone":        "UTC",
  "enabled":         true,
  "timeout_seconds": 60
}
```

Save the returned `job_id` for each job — you need them to pause, resume, or delete jobs later.

### Step 5 — Manage the schedule

**List all active jobs:**

```text
publisher: seren-cron, path: /jobs, method: GET
```

**Pause:**

```text
publisher: seren-cron, path: /jobs/{job_id}/pause, method: POST
```

**Resume:**

```text
publisher: seren-cron, path: /jobs/{job_id}/resume, method: POST
```

**Stop permanently:**

```text
publisher: seren-cron, path: /jobs/{job_id}, method: DELETE
```

### Insufficient Funds Guard

If a live trade or cycle fails because the trading balance or SerenBucks balance is too low to execute, **immediately pause the cron job**:

```text
publisher: seren-cron, path: /jobs/{job_id}/pause, method: POST
```

Then tell the user:

> "Automated trading has been paused: insufficient funds detected. Please top up your balance before resuming the schedule."

Never allow the cron to keep firing when there are no funds available to trade.

## API Key Setup

Before running this skill, check for an existing Seren API key in this order:

1. **Seren Desktop auth** — if the skill is running inside Seren Desktop, the runtime injects `API_KEY` automatically. Check: `echo $API_KEY`. If set, no further action is needed.
2. **Existing `.env` file** — check if `SEREN_API_KEY` is already set in the skill's `.env` file. If set, no further action is needed.
3. **Shell environment** — check if `SEREN_API_KEY` is exported in the current shell. If set, no further action is needed.

**Only if none of the above are set**, register a new agent account:

```bash
curl -sS -X POST "https://api.serendb.com/auth/agent" \
  -H "Content-Type: application/json" \
  -d '{"name":"saas-short-trader"}'
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

## Safety Notes

- Live trading is never auto-enabled.
- Strategy enforces max 8 names and exposure caps.
- If required data feeds fail and strict mode is enabled, run is blocked and persisted as blocked.
- Prefer MCP-native execution in constrained/runtime-sandboxed environments.

## Disclaimer

This skill trades real financial instruments including equities and short positions. Use at your own risk. Short selling carries unlimited loss potential — losses can exceed your initial investment. Past performance does not guarantee future results. This skill does not constitute financial, investment, or tax advice. Only risk capital you can afford to lose. Consult a licensed financial advisor before trading.
