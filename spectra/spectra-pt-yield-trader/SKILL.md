---
name: spectra-pt-yield-trader
description: "Plan and evaluate Spectra PT yield trades using the Spectra MCP server across 10 chains. Use when you need fixed-yield opportunity scans, PT quoting, portfolio simulation, and risk-gated execution handoff."
---

# Spectra Pt Yield Trader

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

## Workflow Summary

1. `validate_inputs` validates chain, size, slippage, and safety caps.
2. `scan_opportunities` uses `mcp-spectra.scan_opportunities` for capital-aware ranking.
3. `select_candidate` filters by symbol, liquidity, maturity window, and impact constraints.
4. `quote_trade` uses `mcp-spectra.quote_trade` for executable PT pricing and min-out.
5. `simulate_portfolio` uses `mcp-spectra.simulate_portfolio_after_trade` to preview deltas.
6. `looping_check` optionally uses `mcp-spectra.get_looping_strategy` for PT+Morpho leverage context.
7. `risk_guard` enforces notional/slippage limits and blocks unsafe requests.
8. `execution_handoff` emits a structured execution intent for a separate signer/executor.

## Key Constraint

The Spectra MCP server is read-only. This skill does not sign or broadcast on-chain transactions.
`live_mode` only controls whether the skill emits an execution handoff payload after passing risk gates.

## Safety Rules

- Default mode is dry-run (`live_mode=false`).
- Handoff requires both:
  - `inputs.live_mode=true`
  - `execution.confirm_live_handoff=true` in config
- Risk caps are enforced before handoff:
  - `policies.max_notional_usd`
  - `policies.max_slippage_bps`
- If any guard fails, return a policy block instead of an execution intent.

## Trade Execution Contract

When the user says `sell`, `close`, `exit`, `unwind`, or `flatten`, emit the execution handoff for the requested PT position immediately or ask only the minimum clarifying question needed to identify the chain and PT address. This skill remains read-only and never signs or broadcasts the transaction itself.

## Pre-Trade Checklist

Before any live handoff:

1. Verify the `mcp-spectra` connector is available and the config passes input validation.
2. Verify `inputs.live_mode=true`, `execution.confirm_live_handoff=true`, and the runtime is started with `--yes-live`.
3. Verify notional and slippage remain inside `policies.max_notional_usd` and `policies.max_slippage_bps`.
4. If any connector, config, or policy check fails, stop here and fail closed instead of emitting a live execution handoff.

## Dependency Validation

Dependency validation is required before live handoff. Verify `mcp-spectra` is available, `SEREN_API_KEY` is loaded when using `seren-cron`, and the execution config names a supported executor type. If the connector is missing, a chain is unsupported, or the handoff config is invalid, the runtime must stop with an error instead of emitting live instructions.

## Live Safety Opt-In

Default mode is dry-run. Live handoff requires all three of the following:

- `inputs.live_mode=true`
- `execution.confirm_live_handoff=true`
- `python scripts/agent.py --config config.json --yes-live`

The `--yes-live` flag is a startup-only opt-in for that process or trigger server. It is not a per-trade approval prompt.

## Emergency Exit Path

To stop trading immediately, run `python scripts/agent.py --config config.json --stop-trading` or send `{"action":"stop-trading"}` to the trigger server. The stop-trading path emits a sell-side unwind handoff for the tracked PT position without requiring an extra `--yes-live` confirmation because this skill never signs or broadcasts the transaction itself.

## Tooling

- Primary connector: `mcp-spectra` publisher backed by the Spectra MCP server (`npx spectra-mcp-server`).
- Optional scheduling connector: `seren-cron` for periodic scans.
- Tool reference: `references/spectra-mcp-tools.md`.

## Quick Start

1. Copy `config.example.json` to `config.json`.
2. Run dry-run planning:
   - `python scripts/agent.py --config config.json`
3. Enable execution handoff only after review:
   - set `inputs.live_mode=true`
   - set `execution.confirm_live_handoff=true`
   - run `python scripts/agent.py --config config.json --yes-live`

## Seren-Cron Integration

Use `seren-cron` to run this skill on a schedule — no terminal windows to keep open, no daemons, no permanent computer changes required. Seren-cron is a cloud scheduler that calls your local trigger server on a cron schedule.

Each scheduled run executes one full planning cycle: scan opportunities, quote candidate PT trade, simulate post-trade portfolio, and emit execution handoff only when enabled by config guards.

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
  "name":            "spectra-pt-yield-trader-live",
  "url":             "http://localhost:8080/run",
  "method":          "POST",
  "cron_expression": "0 */4 * * *",
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

## Disclaimer

This skill involves DeFi yield trading across multiple blockchains. Use at your own risk. DeFi carries smart-contract, oracle, liquidity, and slippage risks. Principal Token values can fluctuate and yields are not guaranteed. Past performance does not guarantee future results. This skill does not constitute financial, investment, or tax advice. Only risk capital you can afford to lose. Consult a licensed financial advisor before trading.
