---
name: p2p-leverage-bitcoin-usdc-deposit
description: "This skill will allow you to deposit 73% of your cash into Polymarket, on Base, with Bitcoin Leverage. Use any supported Payment app on Peer."
---
# P2P Leveraged BTC Deposit

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

## When to Use

- leverage bitcoin to fund polymarket
- p2p cash leveraged bitcoin polymarket deposit
- deposit cash into polymarket with bitcoin leverage
- fund polymarket with cash using bitcoin leverage on base

## What This Skill Does

Converts a cash deposit into a leveraged Bitcoin position that funds a Polymarket trading account. The user keeps long BTC exposure via Aave V3 collateral while deploying borrowed USDC to Polymarket on Polygon.

### Pipeline

```
Cash ($200 via any payment app)
 │
 ▼ ① ZKP2P peer-onramp (fiat → USDC on Base)
USDC on Base
 │
 ▼ ② DEX swap (lowest-spread: Aerodrome or Uniswap V3)
cbBTC on Base
 │
 ▼ ③ Aave V3 Base — supply cbBTC (73% LTV)
 │
 ▼ ④ Aave V3 Base — borrow max USDC
USDC on Base
 │
 ▼ ⑤ Stargate V2 / LayerZero OFT bridge (Base → Polygon)
USDC on Polygon
 │
 ▼ ⑥ Polymarket wallet funded
```

### Example ($200 deposit)

| Step | In | Out |
|---|---|---|
| ① Onramp | $200 cash | ~200 USDC (Base) |
| ② Swap | 200 USDC | ~0.003 cbBTC |
| ③④ Aave | 0.003 cbBTC | ~$146 USDC borrowed |
| ⑤ Bridge | 146 USDC (Base) | ~146 USDC (Polygon) |
| ⑥ Funded | — | Polymarket ready |

**Net position**: Long ~0.003 BTC in Aave vault + ~$146 on Polymarket.
Liquidation if BTC drops ~14.4% from entry.

## Trade Execution Contract

The words **exit**, **close**, **unwind**, **repay**, and **stop** are immediate operator instructions. When the user issues any of these, the agent must:

1. Skip any pending pipeline steps
2. Repay all USDC debt on Aave V3
3. Withdraw all cbBTC collateral
4. Swap cbBTC back to USDC if requested

## Pre-Trade Checklist

Before executing any live transaction the agent must:

1. Verify `POLYMARKET_PRIVATE_KEY` is set and derives a valid address
2. Confirm USDC balance on the wallet (Base) meets `deposit_amount_usd`
3. Probe Base RPC via `seren-base` publisher (`eth_chainId` == `0x2105`)
4. Verify Aave V3 contracts are reachable (Pool, PoolDataProvider)
5. Verify cbBTC reserve is active with LTV > 0 on Aave V3 Base
6. Verify available USDC to borrow on Aave V3 Base
7. Quote DEX swap and check slippage < `max_slippage_bps`
8. Quote Stargate bridge fee
9. Fail-closed if any check fails

## Dependency Validation

All dependencies are validated at startup. If any are missing the agent prints a diagnostic and exits non-zero.

- `POLYMARKET_PRIVATE_KEY` — wallet private key (never logged)
- `SEREN_API_KEY` — Seren gateway API key
- `seren-base` publisher — Base mainnet JSON-RPC
- `seren-polygon` publisher — Polygon mainnet JSON-RPC (for balance confirmation)

## Live Safety Opt-In

**Default mode is `dry-run`.** The agent simulates the full pipeline (quotes, gas estimates, bridge fees) without broadcasting transactions.

To execute live:

```bash
python scripts/agent.py --config config.json --yes-live
```

The `--yes-live` flag is the single live gate. Without it, no transaction is signed or broadcast.

## Emergency Exit Path

```bash
python scripts/agent.py stop --config config.json --yes-live
```

This will:
1. Repay all USDC debt on Aave V3 Base
2. Withdraw all cbBTC collateral from Aave V3 Base
3. Report final balances

## Immediately Run

When this skill is invoked, immediately run:

```bash
python scripts/agent.py --config config.json
```

## Contracts

### Base (Chain ID 8453)

| Contract | Address |
|---|---|
| Aave V3 Pool | `0xA238Dd80C259a72e81d7e4664a9801593F98d1c5` |
| Aave V3 PoolDataProvider | `0x2d8A3C5677189723C4cB8873CfC9C8976FDF38Ac` |
| cbBTC | `0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf` |
| USDC (Base) | `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913` |
| Stargate USDC Pool (Base) | Resolved at setup via Stargate Router |
| Aerodrome Router | `0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43` |

### Polygon (Chain ID 137)

| Contract | Address |
|---|---|
| USDC.e (Polygon) | `0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174` |
| LayerZero Endpoint V2 | `0x1a44076050125825900e736c501f859c50fE728c` |

### LayerZero Endpoint IDs

| Chain | Endpoint ID |
|---|---|
| Base | 30184 |
| Polygon | 30109 |

## Environment Variables

### Polymarket Wallet

| Variable | Required | Description |
| --- | --- | --- |
| `POLYMARKET_WALLET_ADDRESS` | Yes | Your Polymarket wallet address on Base/Polygon |
| `POLYMARKET_PRIVATE_KEY` | Yes | Private key for the wallet above (never logged or committed) |

### Seren Gateway

| Variable | Required | Description |
| --- | --- | --- |
| `SEREN_API_KEY` | Yes | Seren API key — get from Seren Desktop or https://serendb.com |

### ZKP2P Peer Onramp

These are required for Step 1 (fiat deposit). Full onramp instructions at [peer-onramp SKILL.md](https://github.com/zkp2p/zkp2p-skills/blob/main/skills/peer-onramp/SKILL.md).

| Variable | Required | How to Get |
| --- | --- | --- |
| `PRIVATE_KEY` | Yes | Base wallet private key (use same as `POLYMARKET_PRIVATE_KEY`) |
| `WISE_API_TOKEN` | For Wise | Log in to [wise.com](https://wise.com) → Settings → API tokens → Create a personal token (recommended — **100% autonomous**) |
| `VENMO_COOKIES` | For Venmo | Log in to Venmo in your browser → extract `api_access_token`, `v_id`, `login` cookies via DevTools (**80% autonomous**) |
| `PAYPAL_CLIENT_ID` | For PayPal | Create app at [developer.paypal.com](https://developer.paypal.com) → copy Client ID (**100% autonomous**) |
| `PAYPAL_CLIENT_SECRET` | For PayPal | Same PayPal app → copy Secret |

**Payment app autonomy (from [peer-onramp](https://github.com/zkp2p/zkp2p-skills/blob/main/skills/peer-onramp/SKILL.md)):**

| Platform | Agent Autonomy | Auth Setup |
| --- | --- | --- |
| **Wise** | 100% | API token from settings — no 2FA needed |
| **PayPal Business** | 100% | OAuth client ID + secret |
| **Venmo** | 80% | One-time cookie export from browser |
| **Revolut Business** | 70% | Device trust setup needed |
| **CashApp** | 20% | Human sends payment, agent proves + fulfills |
| **Zelle** | 20% | Human sends payment, agent proves + fulfills |

### How the ZKP2P Onramp Works

The onramp runs 6 steps (all handled by the [peer-onramp skill](https://github.com/zkp2p/zkp2p-skills/blob/main/skills/peer-onramp/SKILL.md)):

1. **Get Quote** — find best LP rate for your platform, currency, and amount
2. **Signal Intent** — lock LP's USDC in escrow on-chain (Base)
3. **Send Fiat** — agent sends payment via the payment platform API
4. **Generate Proof** — headless Reclaim proof via `@reclaimprotocol/attestor-core`
5. **Submit Proof** — POST proof to attestation service for EIP-712 signing
6. **Fulfill Intent** — submit attestation on-chain, receive USDC on Base

Setup: `npm install @zkp2p/sdk @reclaimprotocol/attestor-core @zkp2p/providers viem`

## Upstream Skills

- [zkp2p/peer-onramp](https://github.com/zkp2p/zkp2p-skills/blob/main/skills/peer-onramp/SKILL.md) — fiat-to-USDC onramp with headless Reclaim proofs

## Cost Breakdown

| Component | Estimated Cost |
|---|---|
| ZKP2P onramp fee | ~0.1-0.5% of deposit |
| DEX swap fee | ~0.05-0.3% (pool fee + slippage) |
| Aave V3 supply+borrow gas | < $0.01 on Base |
| Stargate bridge fee | ~$0.10-0.50 (LayerZero message fee) |
| **Total overhead** | **~$0.50-2.00 on a $200 deposit** |

## Risks and Disclaimers

- **Liquidation risk**: If BTC price drops ~14.4% below entry, Aave V3 will liquidate the cbBTC collateral (78% threshold, 7.5% bonus).
- **Smart contract risk**: Multiple protocols (Aave, Stargate, DEX) each carry their own contract risk.
- **Bridge delay**: Stargate V2 bridge typically settles in 1-5 minutes but can take longer.
- **Slippage**: Small deposit sizes may experience higher slippage on DEX swaps.
- This skill does not provide financial advice. Users are responsible for their own risk management.
