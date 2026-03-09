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
