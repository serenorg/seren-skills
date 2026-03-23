---
name: smart-dca-bot
description: "AI-optimized Kraken DCA bot with single-asset, portfolio, and scanner modes using local direct execution and strict safety controls."
---

# Kraken Smart DCA Bot

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

AI-assisted dollar-cost averaging (DCA) bot for Kraken with three modes:
- `single_asset`
- `portfolio`
- `scanner`

All trades are executed locally and directly against Kraken REST APIs.

## When to Use

- run smart dca on kraken
- optimize recurring crypto buys
- rebalance dca portfolio allocations
- scan for dca opportunities on kraken

## On Invoke

**Immediately run a dry-run DCA cycle without asking.** Do not present a menu of modes or strategies. Execute:

```bash
cd ~/.config/seren/skills/smart-dca-bot && source .venv/bin/activate && python3 scripts/agent.py --config config.json --accept-risk-disclaimer
```

Display the full dry-run results to the user. Only after results are displayed, present available next steps (live mode, strategy changes). If the user explicitly requests a specific mode in their invocation message, run that mode instead.

## What This Skill Provides

- Mode 1 (`single_asset`) with 5 strategies:
  - `vwap_optimized`
  - `momentum_dip`
  - `spread_optimized`
  - `time_weighted`
  - `simple`
- Mode 2 (`portfolio`) with target allocations and drift detection
- Mode 3 (`scanner`) with four signal families:
  - `oversold_rsi`
  - `volume_spike`
  - `mean_reversion`
  - `new_listing`
  - scanner allocations default to `portfolio.allocations` unless `scanner.base_allocations` is provided
  - scanner approval actions: `pending` (default), `approve`, `modify`, `skip`
- Direct Kraken API integration (no Seren trading proxy)
- First-run Seren API key auto-registration (`SEREN_API_KEY`)
- Optional SerenDB schema + persistence (`SERENDB_URL`)
- JSONL audit logging (`logs/*.jsonl`)
- Cost-basis lot tracking (`state/cost_basis_lots.json`)
- Dry-run mode by default
- Cron/webhook support (`run_agent_server.py`, `setup_cron.py`)

## API Key Setup

Before running this skill, check for an existing Seren API key in this order:

1. **Seren Desktop auth** — if the skill is running inside Seren Desktop, the runtime injects `API_KEY` automatically. Check: `echo $API_KEY`. If set, no further action is needed.
2. **Existing `.env` file** — check if `SEREN_API_KEY` is already set in the skill's `.env` file. If set, no further action is needed.
3. **Shell environment** — check if `SEREN_API_KEY` is exported in the current shell. If set, no further action is needed.

**Only if none of the above are set**, register a new agent account:

```bash
curl -sS -X POST "https://api.serendb.com/auth/agent" \
  -H "Content-Type: application/json" \
  -d '{"name":"smart-dca-bot"}'
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

## Setup

1. Copy `.env.example` to `.env` and fill credentials.
2. Copy `config.example.json` to `config.json`.
3. Install dependencies:
   - `pip install -r requirements.txt`
4. Initialize SerenDB schema (optional, requires `SERENDB_URL`):
   - `python scripts/setup_serendb.py`
5. Run dry mode:
   - `python scripts/agent.py --config config.json --accept-risk-disclaimer`
6. Run live mode (explicit opt-in only):
   - set `"dry_run": false` in `config.json`
   - `python scripts/agent.py --config config.json --allow-live --accept-risk-disclaimer`

## Workflow Summary

1. Validate config and policy caps.
2. Ensure `SEREN_API_KEY` (validate existing or auto-register on first run).
3. Build DCA window and market snapshot(s).
4. Select strategy decision and risk-gate execution.
5. Execute locally to Kraken (or simulate in dry-run).
6. Persist runs, snapshots, scanner signals, and cost-basis lots.
7. Emit JSONL audit events.

## Trade Execution Contract

When the user says `sell`, `close`, `exit`, `unwind`, or `flatten`, stop new DCA entries immediately, cancel tracked pending Kraken orders, and ask only the minimum clarifying question needed if the user also wants held spot inventory liquidated.

## Pre-Trade Checklist

Before any live `run --allow-live --accept-risk-disclaimer` or `loop --allow-live --accept-risk-disclaimer` execution:

1. Verify `SEREN_API_KEY` and Kraken API credentials are loaded.
2. Verify the requested notional, balances, and cash reserve fit the account.
3. Verify Python dependencies from `requirements.txt` are installed and the venue client can load.
4. If any credential, dependency, or balance probe fails, stop here and fail closed instead of placing orders.

## Dependency Validation

Dependency validation is required before live trading. Verify `SEREN_API_KEY`, Kraken credentials, and Python dependencies from `requirements.txt` are installed and loaded. `SERENDB_URL` is optional, but if exchange credentials are missing or the Kraken client cannot be initialized, the runtime must stop with an error instead of submitting orders.

## Emergency Exit Path

To stop trading immediately, run `python scripts/agent.py stop-trading --config config.json`. The stop-trading path cancels tracked pending Kraken orders without asking for an extra live confirmation, writes the remaining local state to disk, and leaves held spot positions untouched until the operator chooses how to liquidate them.

## Required Disclaimers

IMPORTANT DISCLAIMERS — READ BEFORE USING

1. NOT FINANCIAL ADVICE: This skill is a software tool, not a financial advisor.
   It does not provide investment, financial, tax, or legal advice. All trading
   decisions are made by you. Consult a licensed financial advisor before investing.

2. RISK OF LOSS: Cryptocurrency trading involves substantial risk of loss. Prices
   can decline significantly. You may lose some or all of your invested capital.
   Only invest money you can afford to lose entirely.

3. NO GUARANTEES: Past performance does not guarantee future results. The
   optimization algorithms attempt to improve execution timing but cannot guarantee
   better prices than naive DCA. Market conditions may render optimizations
   ineffective.

4. LOCAL EXECUTION ONLY: All trades are executed locally on your machine, directly
   to the Kraken API using your personal API credentials. No trades flow through
   Seren Gateway or any third-party intermediary. SerenAI does not have access to
   your Kraken account, funds, or trading activity.

5. API KEY SECURITY: Your Kraken API keys are stored locally in your .env file and
   are never transmitted to SerenAI servers. You are responsible for securing your
   API credentials. Use IP whitelisting and withdrawal restrictions on Kraken.

6. EXCHANGE RISK: This skill depends on Kraken's API availability. Exchange
   outages, maintenance windows, or API changes may affect execution. The skill
   includes fallback logic but cannot guarantee execution during exchange issues.

7. TAX IMPLICATIONS: Each DCA purchase creates a taxable lot in many jurisdictions.
   You are responsible for tracking cost basis and reporting to tax authorities.
   The cost_basis_lots table is provided for convenience but is not tax advice.

8. REGULATORY COMPLIANCE: Cryptocurrency regulations vary by jurisdiction. You are
   responsible for ensuring compliance with all applicable laws and regulations in
   your jurisdiction.

9. SOFTWARE PROVIDED AS-IS: This skill is provided "as is" without warranty of any
   kind. The authors and SerenAI are not liable for any losses, damages, or costs
   arising from the use of this software.

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

### Step 4 — Create the cron schedule

With the server running, create the scheduled job:

```text
publisher: seren-cron
path:      /jobs
method:    POST
body: {
  "name":            "kraken-smart-dca-live",
  "url":             "http://localhost:8080/run",
  "method":          "POST",
  "cron_expression": "0 */6 * * *",
  "timezone":        "UTC",
  "enabled":         true,
  "timeout_seconds": 60
}
```

Save the returned `job_id` — you need it to pause, resume, or delete the job later.

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
