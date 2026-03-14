---
name: curve-gauge-yield-trader
description: "Multi-chain Curve gauge yield trading skill with paper-first defaults. Supports local wallet generation or Ledger signer mode for live execution."
---

# Curve Gauge Yield Trader

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

## When to Use

- find the best curve gauge rewards
- paper trade curve gauge liquidity
- trade live on curve gauges

## Workflow Summary

1. `fetch_top_gauges` uses `connector.curve_api.get` (`/getGauges`)
2. `choose_trade` uses `transform.select_best_gauge`
3. `signer_setup` uses `transform.setup_signer`
4. `rpc_discovery` resolves chain RPC publisher from gateway catalog (`GET /publishers`)
5. `preflight` builds and estimates local EVM transactions via chain RPC (no cloud signer)
6. `live_guard` uses `transform.guard_live_execution`
7. `execute_liquidity_trade` signs locally and submits with `eth_sendRawTransaction`

## Funding and Safety

- Default mode is dry-run.
- Live transactions require both:
  - `inputs.live_mode = true` in config
  - `--yes-live` on the CLI
- Live mode uses real funds. Only fund what you can afford to lose.
- Each run resolves the RPC publisher from the live Seren publisher catalog (`GET /publishers`) and performs an explicit probe before preflight/trade.
  - If probe fails, execution stops early with a clear unsupported-chain/RPC error.
- Optional override: set `rpc_publishers` in config (`{ "ethereum": "<slug>" }`) to force a specific publisher slug per chain.
- Transactions are prepared and signed locally.
  - `wallet_mode=local`: agent signs with local private key.
  - `wallet_mode=ledger`: preflight creates unsigned txs; you provide signed raw txs in `evm_execution.ledger.signed_raw_transactions` for broadcast.

## Disclaimer

This skill can trade real money. Use at your own risk. Past performance does not guarantee future results.

- DeFi carries smart-contract, oracle, liquidity, and slippage risks.
- RPC/provider outages or stale data can cause failed or unfavorable execution.
- You are responsible for wallet security, transaction approvals, and chain/network selection.
- Start in dry-run, test with small live size, and scale only after repeated stable runs.

## Wallet Modes

- `wallet_mode=local`: generate a local wallet with `--init-wallet`, then fund that address.
- `wallet_mode=ledger`: provide Ledger address and use preflight output to sign externally.

## Local Execution Config

- Default strategy is `evm_execution.strategy = "gauge_stake_lp"`.
  - Requires `lp_token_address` and `lp_amount_wei` if they cannot be derived from market data.
  - Optional `gauge_address` override.
- For fully custom calls, use `evm_execution.strategy = "custom_tx"` and set:
  - `evm_execution.custom_tx.to`
  - `evm_execution.custom_tx.data`
  - `evm_execution.custom_tx.value_wei`
- Gas behavior is controlled with:
  - `evm_execution.tx.gas_price_multiplier`
  - `evm_execution.tx.gas_limit_multiplier`
  - `evm_execution.tx.fallback_gas_limit`

## Quick Start

1. Create/get your `SEREN_API_KEY` by following [https://docs.serendb.com/skills.md](https://docs.serendb.com/skills.md), then set it in your environment (for example: `export SEREN_API_KEY=...`).
2. Copy `config.example.json` to `config.json`.
3. Install dependencies:
   - `pip install -r requirements.txt`
4. Generate local wallet (optional):
   - `python scripts/agent.py --init-wallet --wallet-path state/wallet.local.json`
5. Dry-run preflight:
   - `python scripts/agent.py --config config.json`
6. Live mode (only after funding and signer validation):
   - set `inputs.live_mode=true` in config
   - `python scripts/agent.py --config config.json --yes-live`

## Seren-Cron Integration

Use `seren-cron` to run this skill on a schedule — no terminal windows to keep open, no daemons, no permanent computer changes required. Seren-cron is a cloud scheduler that calls your local trigger server on a cron schedule.

Each scheduled run executes one full cycle: sync positions, fetch top gauges, build local preflight txs, and execute if live mode is enabled and `--yes-live` is set on the server process.

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
  "name":            "curve-gauge-yield-trader-live",
  "url":             "http://localhost:8080/run",
  "method":          "POST",
  "cron_expression": "*/30 * * * *",
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
