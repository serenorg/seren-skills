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

1. `fetch_top_gauges` uses `connector.curve_api.get`
2. `choose_trade` uses `transform.select_best_gauge`
3. `signer_setup` uses `transform.setup_signer`
4. `preflight` uses `connector.evm_exec.post`
5. `live_guard` uses `transform.guard_live_execution`
6. `execute_liquidity_trade` uses `connector.evm_exec.post`

## Funding and Safety

- Default mode is dry-run.
- Live transactions require both:
  - `inputs.live_mode = true` in config
  - `--yes-live` on the CLI
- Live mode uses real funds. Only fund what you can afford to lose.
- Each run resolves the RPC publisher from the live Seren publisher catalog (`GET /publishers`) and performs an explicit probe before preflight/trade.
  - If probe fails, execution stops early with a clear unsupported-chain/RPC error.
- Optional override: set `rpc_publishers` in config (`{ "ethereum": "<slug>" }`) to force a specific publisher slug per chain.

## Wallet Modes

- `wallet_mode=local`: generate a local wallet with `--init-wallet` and fund it.
- `wallet_mode=ledger`: provide a Ledger EVM address and sign through your hardware wallet flow.

## Quick Start

1. Copy `.env.example` to `.env` and set `SEREN_API_KEY`.
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
- preflight
- execute if live mode is enabled and `--yes-live` is set on the server process
