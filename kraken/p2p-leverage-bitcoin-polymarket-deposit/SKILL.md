---
name: p2p-leverage-bitcoin-polymarket-deposit
description: "Deposit up to 5x leveraged cash into Polymarket using Kraken margin trading. Fiat onramp via Kraken Ramp API. Uses Kraken REST API directly — user supplies their own API keys."
---

# P2P Cash Leveraged Bitcoin Polymarket Deposits (Kraken)

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

## When to Use

- leverage bitcoin on kraken to fund polymarket
- kraken margin leveraged bitcoin polymarket deposit
- deposit cash into polymarket with kraken bitcoin leverage
- fund polymarket using kraken 5x margin

## What This Skill Does

Converts a cash deposit into a 5x leveraged Bitcoin position on Kraken, then withdraws borrowed USDC directly to a Polymarket wallet on Polygon. Uses Kraken Ramp API for fiat onramp — no external onramp service needed.

### Pipeline

```text
Cash ($200 via bank/card)
 │
 ▼ ① Kraken Ramp API — buy USDC with fiat (payment-methods → quote → checkout)
USDC on Kraken
 │
 ▼ ② Buy BTC on 5x margin (API: AddOrder with leverage:5)
BTC position on Kraken (5x leveraged)
 │
 ▼ ③ Withdraw borrowed USDC to Polygon (API: Withdraw)
USDC on Polygon
 │
 ▼ ④ Polymarket wallet funded
```

### Example ($200 deposit at 5x leverage)

| Step | In | Out |
| --- | --- | --- |
| ① Kraken Ramp | $200 fiat | ~200 USDC on Kraken |
| ② Margin Buy 5x | 200 USDC collateral | ~$1,000 BTC position |
| ③ Withdraw | Borrowed ~$800 USDC | ~$800 USDC on Polygon |
| ④ Funded | — | Polymarket ready |

**Net position**: Long ~$1,000 BTC on Kraken (5x) + ~$800 on Polymarket.

### Leverage Options

| Leverage | BTC Position | Borrowed USDC | To Polymarket | Liquidation Buffer |
| --- | --- | --- | --- | --- |
| 2x | $400 | ~$200 | ~$200 | ~50% drop |
| 3x | $600 | ~$400 | ~$400 | ~33% drop |
| 5x | $1,000 | ~$800 | ~$800 | ~20% drop |

## Trade Execution Contract

The words **exit**, **close**, **unwind**, **repay**, and **stop** are immediate operator instructions. When the user issues any of these, the agent must:

1. Skip any pending pipeline steps
2. Close the margin position on Kraken (sell BTC, repay borrowed funds)

## Pre-Trade Checklist

Before executing any live transaction the agent must:

1. Verify `KRAKEN_API_KEY` and `KRAKEN_API_SECRET` are set
2. Verify Kraken API connectivity (`SystemStatus`)
3. Check account balance via `Balance`
4. Check margin eligibility via `TradeBalance`
5. Verify Kraken Ramp payment methods are available (`/b2b/ramp/payment-methods`)
6. Verify BTC/USD margin pair is available with requested leverage
7. Verify Polygon USDC withdrawal is available (`WithdrawMethods`)
8. Fail-closed if any check fails

## Dependency Validation

All dependencies are validated at startup. If any are missing the agent prints a diagnostic and exits non-zero.

- `KRAKEN_API_KEY` — Kraken API key with **Trade** and **Funds** permissions
- `KRAKEN_API_SECRET` — Kraken API secret (base64 encoded)
- `POLYMARKET_WALLET_ADDRESS` — destination wallet address on Polygon
- `SEREN_API_KEY` — Seren gateway key (for RPC balance confirmation only)

## Live Safety Opt-In

**Default mode is `dry-run`.** The agent queries Kraken for quotes, balances, and fee estimates without placing orders or initiating transfers.

To execute live:

```bash
python scripts/agent.py --config config.json --yes-live
```

The `--yes-live` flag is the single live gate. Without it, no orders are placed and no withdrawals are initiated.

## Emergency Exit Path

```bash
python scripts/agent.py stop --config config.json --yes-live
```

This will:

1. Close all open BTC margin positions on Kraken
2. Report final balances

## Immediately Run

When this skill is invoked, immediately run:

```bash
python scripts/agent.py --config config.json
```

## Kraken API Endpoints Used

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/0/public/SystemStatus` | GET | Health check |
| `/0/public/Ticker` | GET | BTC/USD price |
| `/0/public/AssetPairs` | GET | Verify margin pair availability |
| `/b2b/ramp/payment-methods` | GET | Available fiat payment methods |
| `/b2b/ramp/quotes/prospective` | GET | Get fiat-to-USDC quote |
| `/b2b/ramp/checkout` | GET | Execute fiat purchase |
| `/0/private/Balance` | POST | Account balances |
| `/0/private/TradeBalance` | POST | Margin status and equity |
| `/0/private/AddOrder` | POST | Place margin buy order (with `leverage` param) |
| `/0/private/Withdraw` | POST | Withdraw USDC to Polygon address |
| `/0/private/WithdrawInfo` | POST | Withdrawal fee estimate |
| `/0/private/OpenPositions` | POST | Check open margin positions |
| `/0/private/CancelOrder` | POST | Cancel pending orders |

## Environment Variables

### Kraken API

| Variable | Required | How to Get |
| --- | --- | --- |
| `KRAKEN_API_KEY` | Yes | [kraken.com](https://www.kraken.com) → Settings → API → Create Key → enable **Query Funds**, **Deposit Funds**, **Trade**, **Withdraw Funds** |
| `KRAKEN_API_SECRET` | Yes | Shown once when creating the API key above — save it immediately |

### Polymarket Wallet

| Variable | Required | Description |
| --- | --- | --- |
| `POLYMARKET_WALLET_ADDRESS` | Yes | Your Polymarket wallet address on Polygon (find at polymarket.com/wallet) |

### Seren Gateway

| Variable | Required | Description |
| --- | --- | --- |
| `SEREN_API_KEY` | Yes | Seren API key — get from Seren Desktop or [serendb.com](https://serendb.com) |

## Kraken Account Requirements

- **KYC**: Intermediate verification or higher required for margin trading
- **Geographic**: US, UK, and Canada have restrictions — check [Kraken eligibility](https://support.kraken.com/articles/4402532394260-client-eligibility-for-margin-trading-services-)
- **API permissions**: The API key must have **Query Funds**, **Deposit Funds**, **Trade**, and **Withdraw Funds** enabled
- **Margin**: Account must be approved for margin trading (automatic with Intermediate+ verification in eligible regions)

## Cost Breakdown

| Component | Estimated Cost |
| --- | --- |
| Kraken Ramp fee | Varies by payment method (card ~1.5%, bank ~0%) |
| Kraken trading fee | 0.16-0.26% (maker/taker) |
| Kraken margin open fee | 0.01-0.05% |
| Kraken withdrawal fee | ~1 USDC (Polygon) |
| **Total overhead** | **~$1-4 on a $200 deposit** |

## Risks and Disclaimers

- **Liquidation risk**: At 5x leverage, a ~20% BTC drop triggers liquidation. Kraken may liquidate at their discretion when margin level falls to 40-80%.
- **Custodial**: BTC collateral is held by Kraken, not in your own wallet.
- **KYC required**: Kraken requires identity verification for margin trading.
- **Geographic restrictions**: Margin trading is not available in all jurisdictions.
- This skill does not provide financial advice. Users are responsible for their own risk management.
