---
name: euler-base-vault-bot
description: "Deposit USDC into the AlphaGrowth Base Vault on Euler Finance, collect Supply APY, and periodically compound rewards. Supports dry-run and live execution with local wallet or Ledger signer."
---

# Euler Base Vault Bot

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

## When to Use

- deposit USDC into the AlphaGrowth Euler vault on Base
- check AlphaGrowth vault position and APY
- compound Euler vault rewards
- withdraw from AlphaGrowth Base vault

## Workflow Summary

1. `probe_rpc` uses `connector.rpc_base.post`
2. `read_vault_state` uses `connector.rpc_base.post`
3. `read_position` uses `connector.rpc_base.post`
4. `build_transactions` uses `transform.create_plan`
5. `estimate_gas` uses `connector.rpc_base.post`
6. `live_guard` uses `transform.guard_live_execution`
7. `execute_transactions` uses `connector.rpc_base.post`

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
  "name":            "euler-vault-compound-live",
  "url":             "http://localhost:8080/run",
  "method":          "POST",
  "cron_expression": "0 */12 * * *",
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
