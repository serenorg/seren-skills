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
   - run `python scripts/agent.py --config config.json`

## Autonomous Scheduling with seren-cron

1. Start trigger server:
   - `python scripts/run_agent_server.py --config config.json --port 8080`
2. Create cron job:
   - `python scripts/setup_cron.py create --url http://localhost:8080/run --schedule "*/30 * * * *"`
3. Manage jobs:
   - `python scripts/setup_cron.py list`
   - `python scripts/setup_cron.py pause --job-id <job_id>`
   - `python scripts/setup_cron.py resume --job-id <job_id>`
   - `python scripts/setup_cron.py delete --job-id <job_id>`

Each scheduled run executes one full planning cycle:
- scan opportunities
- quote candidate PT trade
- simulate post-trade portfolio
- emit execution handoff only when enabled by config guards

## Disclaimer

This skill involves DeFi yield trading across multiple blockchains. Use at your own risk. DeFi carries smart-contract, oracle, liquidity, and slippage risks. Principal Token values can fluctuate and yields are not guaranteed. Past performance does not guarantee future results. This skill does not constitute financial, investment, or tax advice. Only risk capital you can afford to lose. Consult a licensed financial advisor before trading.
