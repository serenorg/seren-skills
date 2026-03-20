# Glend Agent Skill

> **DeFi Lend & Borrow** for AI agents — supply, borrow, repay, and withdraw assets on Pharos Testnet (Aave V3), Ethereum, and Base (Compound V2).

## What is Glend?

[Glend](https://glendv2.gemach.io) is a decentralized lending and borrowing protocol by GemachDAO. It runs as an Aave V3 fork on Pharos Testnet and a Compound V2 fork on Ethereum and Base, giving agents a unified interface for DeFi lending across multiple chains.

## Source Repo

Full documentation, ABIs, and source: **[GemachDAO/glend-skill](https://github.com/GemachDAO/glend-skill)**

## Quick Install

```bash
npx skills add GemachDAO/glend-skill
```

## Directory Layout

```
gemachdao/glend/
├── SKILL.md                              # Full agent documentation (ABIs, code examples, workflows)
├── README.md                             # This file
├── .env.example                          # Environment variable template
├── .gitignore                            # Ignore secrets and artifacts
└── scripts/
    ├── e2e-seren-integration.test.sh     # End-to-end integration tests
    └── smoke-test.sh                     # Quick offline validation
```

## Agent Capabilities

| Operation | Pharos (Aave V3) | Ethereum/Base (Compound V2) |
|---|---|---|
| Supply / Lend | `supplyAsset()` | `compoundSupply()` |
| Borrow | `borrowAsset()` | `compoundBorrow()` |
| Repay | `repayDebt()` | `compoundRepay()` |
| Withdraw | `withdrawAsset()` | `compoundWithdraw()` |
| Check health | `getAccountHealth()` | `getCompoundAccountHealth()` |
| Market data | `getReserveData()` | `getCompoundMarketRates()` |
| Test tokens | `mintTestTokens()` | N/A |

## Pre-configured Deployments

| Chain | Chain ID | Protocol | Pool / Comptroller |
|---|---|---|---|
| Pharos Testnet | 688688 | Aave V3 fork | `0xe838eb8011297024bca9c09d4e83e2d3cd74b7d0` |
| Ethereum Mainnet | 1 | Compound V2 fork | `0x4a4c2A16b58bD63d37e999fDE50C2eBfE3182D58` |
| Base | 8453 | Compound V2 fork | `0x4a4c2A16b58bD63d37e999fDE50C2eBfE3182D58` |

## Required Environment Variables

| Variable | Description |
|---|---|
| `AGENT_PRIVATE_KEY` | Wallet private key — **required**, never commit |
| `GLEND_CHAIN_ID` | Chain ID (default: `688688` / Pharos Testnet) |
| `GLEND_RPC_URL` | Override default RPC URL |
| `GLEND_POOL_ADDRESS` | Override default pool/comptroller address |

Copy `.env.example` to `.env` and fill in your values.

## Compatibility

Works with all [skills.sh](https://skills.sh) agent frameworks:
- Claude Code
- Cursor
- GitHub Copilot
- Windsurf
- OpenCode
- Any agent that reads `SKILL.md`

See [`SKILL.md`](./SKILL.md) for full contract ABIs, TypeScript code examples, safety rules, and step-by-step workflows.
