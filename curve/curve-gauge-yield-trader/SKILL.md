---
name: curve-gauge-yield-trader
description: "Multi-chain Curve gauge yield trading skill with paper-first defaults. Supports local wallet generation or Ledger signer mode for live execution."
---

# Curve Gauge Yield Trader

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

Each scheduled run executes one full cycle:
- sync positions
- fetch top gauges
- build local preflight txs
- execute if live mode is enabled and `--yes-live` is set on the server process
