# GDEX Trading Skill

This skill integrates the [GDEX Trading SDK](https://github.com/GemachDAO/gdex-skill) into Seren Desktop, enabling AI agents to execute cross-chain DeFi operations via managed-custody wallets.

## What This Skill Provides

- Spot trading (buy/sell) on Solana, Sui, Ethereum, Base, Arbitrum, BSC, and 12+ EVM chains
- Perpetual futures: open/close positions, set leverage, manage margin
- Portfolio management: balances, trade history, open positions
- Token discovery: trending tokens, OHLCV price data, token details
- Cross-chain bridging: estimate and execute asset bridges
- Limit orders: place, update, and cancel
- Copy trading: follow top-performing traders

## Source

Full source code, sub-skill documentation, and examples:
👉 [GemachDAO/gdex-skill](https://github.com/GemachDAO/gdex-skill)

npm package: [`@gdexsdk/gdex-skill`](https://www.npmjs.com/package/@gdexsdk/gdex-skill)

## Quick Start

1. Install dependencies:
   ```bash
   npm install
   ```

2. Copy `.env.example` to `.env` and fill in your credentials:
   ```bash
   cp .env.example .env
   ```

3. Verify the SDK is working offline:
   ```bash
   node scripts/verify.js
   ```

4. Run the end-to-end integration tests:
   ```bash
   node scripts/e2e-seren-integration.test.js
   ```

5. Validate the SKILL.md frontmatter:
   ```bash
   node scripts/validate-skill.js
   ```

## Directory Layout

```
gdex/trading/
├── SKILL.md                            # Skill documentation and frontmatter
├── README.md                           # This file
├── package.json                        # npm dependencies
├── .env.example                        # Environment variable template
└── scripts/
    ├── verify.js                       # Offline SDK smoke test
    ├── e2e-seren-integration.test.js   # End-to-end integration tests
    └── validate-skill.js              # SKILL.md frontmatter validator
```

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `GDEX_API_KEY` | Yes (for live trading) | Shared API key from GDEX dashboard |
| `CONTROL_WALLET_PRIVATE_KEY` | Yes (for live trading) | Control wallet private key for managed-custody signing |

> **Note**: The `walletAddress` field in API calls must be your **control** address, not the managed address.

## Supported Chains

| Chain | ChainId |
|---|---|
| Ethereum | 1 |
| Base | 8453 |
| Arbitrum | 42161 |
| BSC | 56 |
| Avalanche | 43114 |
| Polygon | 137 |
| Optimism | 10 |
| Solana | 622112261 |
| Sui | 101 |
