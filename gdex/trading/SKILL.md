---
name: trading
description: "Cross-chain DeFi trading skill for AI agents — spot trading, perpetual futures, portfolio management, and token discovery on Solana, Sui, and 12+ EVM chains via managed-custody wallets."
license: MIT
metadata:
  source: "https://github.com/GemachDAO/gdex-skill"
  npm: "@gdexsdk/gdex-skill"
  version: "2.0.0"
---

# GDEX Trading

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

## Overview

GDEX Trading gives AI agents access to cross-chain DeFi operations via managed-custody wallets. Agents can trade spot markets, open and manage perpetual futures positions, bridge assets, discover tokens, and monitor portfolios — all through a single unified API at `https://trade-api.gemach.io/v1`.

Supported chains: Solana, Sui, Ethereum, Base, Arbitrum, BSC, Avalanche, Polygon, and 8+ additional EVM networks.

## Available Sub-Skills

| Sub-skill | Purpose |
|---|---|
| `gdex-authentication` | API key login, session management |
| `gdex-onboarding` | New user wallet setup and verification |
| `gdex-wallet-setup` | Wallet configuration and key management |
| `gdex-spot-trading` | Market buy/sell on any supported chain |
| `gdex-perp-trading` | Open, close, and manage perp positions |
| `gdex-perp-funding` | Deposit/withdraw perp margin |
| `gdex-perp-copy-trading` | Copy top-trader perp strategies |
| `gdex-copy-trading` | Copy top-trader spot strategies |
| `gdex-limit-orders` | Place, update, and cancel limit orders |
| `gdex-portfolio` | View balances, positions, trade history |
| `gdex-token-discovery` | Trending tokens, OHLCV, token details |
| `gdex-bridge` | Estimate and execute cross-chain bridges |
| `gdex-ui-install-setup` | Frontend SDK install and setup |
| `gdex-ui-page-layouts` | UI page layout components |
| `gdex-ui-portfolio-dashboard` | Portfolio dashboard UI |
| `gdex-ui-theming` | Theme and styling configuration |
| `gdex-ui-trading-components` | Trading UI components |
| `gdex-ui-wallet-connection` | Wallet connection UI |
| `gdex-sdk-debugging` | SDK debugging and error diagnostics |

## Quick Decision Guide

- **Buy or sell a token** → `gdex-spot-trading`
- **Open a leveraged position** → `gdex-perp-trading`
- **Bridge assets cross-chain** → `gdex-bridge`
- **Find trending tokens or price data** → `gdex-token-discovery`
- **Check portfolio or balances** → `gdex-portfolio`
- **Place limit orders** → `gdex-limit-orders`
- **Follow top traders** → `gdex-copy-trading` or `gdex-perp-copy-trading`
- **First-time setup** → `gdex-authentication` then `gdex-onboarding`

## Installation

```bash
npm install @gdexsdk/gdex-skill
```

## Authentication

GDEX uses a two-layer auth model:

1. **API Key** — shared keys available via the GDEX dashboard or community keys. Set `GDEX_API_KEY` in your environment or call `loginWithApiKey(apiKey)`.
2. **Managed-custody signing** — trading actions require a secp256k1 session key derived from your control wallet's private key (`CONTROL_WALLET_PRIVATE_KEY`). The SDK handles AES-256-CBC payload encryption and SHA256 key derivation automatically.

```js
import { GdexSkill } from '@gdexsdk/gdex-skill';

const skill = new GdexSkill();
await skill.loginWithApiKey(process.env.GDEX_API_KEY);
```

## Critical Notes

- **`walletAddress` is your CONTROL address, NOT the managed address.** Always use the control wallet address for authentication and identity fields.
- **Solana `chainId` is `622112261`**, not `900`. Using the wrong value causes silent failures.
- **`hlCloseAll` is unreliable** — use reduce-only orders instead to close Hyperliquid perp positions.
- **`hlUpdateLeverage`** is not implemented on the backend; use `set_perp_leverage` instead.

## Environment Variables

```
GDEX_API_KEY=                    # Shared API key
CONTROL_WALLET_PRIVATE_KEY=      # Control wallet private key (for managed-custody trading)
```

## Source Repository

Full SDK source, examples, and sub-skill implementations:
[https://github.com/GemachDAO/gdex-skill](https://github.com/GemachDAO/gdex-skill)
