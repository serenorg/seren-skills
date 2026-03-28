---
name: p2p-hyperliquid-bitcoin-usdc-deposit
description: "Deposit cash into Polymarket with 5x Bitcoin leverage on Hyperliquid. No KYC, no debt, self-custody. Fiat onramp via ZKP2P peer-onramp. Dry-run includes 270-day liquidation risk backtest."
---

# P2P Hyperliquid Bitcoin USDC Polymarket Deposit

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

## When to Use

- deposit cash into polymarket with hyperliquid bitcoin leverage
- p2p hyperliquid leveraged bitcoin polymarket deposit
- fund polymarket using hyperliquid 5x perp no kyc

## What This Skill Does

Converts a cash deposit into a 5x leveraged BTC-PERP on Hyperliquid, then withdraws free USDC directly to Polymarket on Polygon via CCTP. No debt is created — the Polymarket funds are free from day one. All Hyperliquid API calls route through the `seren-hyperliquid` publisher. Backtest uses `coingecko-serenai` publisher for BTC prices.

### Pipeline

```text
Cash ($200 via any payment app)
 │
 ▼ ① ZKP2P peer-onramp (fiat → USDC on Base)
USDC on Base
 │
 ▼ ② CCTP deposit USDC to Hyperliquid
USDC on Hyperliquid
 │
 ▼ ③ Open 5x BTC-PERP ($40 margin → $200 notional)
 │
 ▼ ④ Withdraw $159 free USDC → CCTP → Polygon
 │
 ▼ ⑤ Polymarket wallet funded ($0 debt)
```

### Example ($200 deposit at 5x)

| Step | In | Out |
| --- | --- | --- |
| ① ZKP2P Onramp | $200 cash | ~200 USDC (Base) |
| ② CCTP Deposit | 200 USDC | Hyperliquid balance |
| ③ Open 5x Perp | $40 margin | $200 BTC-PERP position |
| ④ Withdraw | $159 free USDC | USDC on Polygon |
| ⑤ Funded | — | Polymarket ready |

**Net position**: Long $200 BTC (5x) + $159 on Polymarket. **$0 debt.**

### Liquidation on Hyperliquid

If liquidated: you lose the $40 margin. You keep the $159 on Polymarket. **Net: +$119 even after liquidation.**

## Trade Execution Contract

The words **exit**, **close**, **unwind**, and **stop** are immediate operator instructions. When the user issues any of these, the agent must close the BTC-PERP position immediately.

## Pre-Trade Checklist

1. Verify `POLYMARKET_PRIVATE_KEY` is set
2. Verify `SEREN_API_KEY` is set
3. Confirm USDC balance on Hyperliquid
4. Verify BTC-PERP market is available
5. Run 270-day liquidation risk backtest and display results
6. Fail-closed if any check fails

## Dependency Validation

- `POLYMARKET_PRIVATE_KEY` — wallet private key for Hyperliquid signing and Polymarket
- `SEREN_API_KEY` — Seren gateway key (CoinGecko publisher for backtest)

## Live Safety Opt-In

**Default mode is `dry-run`.** Dry-run simulates the full pipeline and runs the 270-day liquidation risk backtest using CoinGecko historical BTC prices. No positions opened, no USDC moved.

```bash
python scripts/agent.py --config config.json
```

To execute live:

```bash
python scripts/agent.py --config config.json --yes-live
```

## Emergency Exit Path

```bash
python scripts/agent.py stop --config config.json --yes-live
```

Closes the BTC-PERP position and withdraws remaining margin.

## Immediately Run

When this skill is invoked, immediately run:

```bash
python scripts/agent.py --config config.json
```

## Liquidation Risk Backtest

The dry-run automatically fetches 270 days of BTC prices from CoinGecko (via Seren publisher) and simulates liquidation risk:

| Leverage | Liq Threshold | Liq Rate (270d, 30d window) | Risk Rating |
| --- | --- | --- | --- |
| 5x | 17% drop | ~20% | HIGH |
| 10x | 7% drop | ~44% | VERY HIGH |
| 50x | 1% drop | ~89% | VERY HIGH |

**Default is 5x.** At 5x, 1 in 5 entries over the past 270 days would have been liquidated within 30 days.

## Environment Variables

### Wallet

| Variable | Required | Description |
| --- | --- | --- |
| `POLYMARKET_PRIVATE_KEY` | Yes | Wallet private key — used for Hyperliquid signing and as Polymarket wallet |

### Seren Gateway

| Variable | Required | Description |
| --- | --- | --- |
| `SEREN_API_KEY` | Yes | Seren API key — used for CoinGecko publisher (backtest) |

### ZKP2P Peer Onramp

Required for Step 1 (fiat deposit). Full instructions at [peer-onramp SKILL.md](https://github.com/zkp2p/zkp2p-skills/blob/main/skills/peer-onramp/SKILL.md).

| Variable | Required | How to Get |
| --- | --- | --- |
| `PRIVATE_KEY` | Yes | Base wallet private key (use same as `POLYMARKET_PRIVATE_KEY`) |
| `WISE_API_TOKEN` | For Wise | [wise.com](https://wise.com) → Settings → API tokens (recommended — **100% autonomous**) |
| `VENMO_COOKIES` | For Venmo | Browser DevTools → extract session cookies (**80% autonomous**) |

## Upstream Skills

- [zkp2p/peer-onramp](https://github.com/zkp2p/zkp2p-skills/blob/main/skills/peer-onramp/SKILL.md) — fiat-to-USDC onramp

## Cost Breakdown

| Component | Estimated Cost |
| --- | --- |
| ZKP2P onramp fee | ~0.1-0.5% of deposit |
| Hyperliquid trading fee | 0.035% (taker) |
| CCTP withdrawal | ~$1 USDC |
| **Total** | **~$1-2 on $200** |

## Risks and Disclaimers

- **Liquidation risk**: At 5x, a ~17% BTC drop triggers liquidation. You lose the $40 margin but keep Polymarket funds.
- **No debt**: Nothing is borrowed. Polymarket funds are free immediately.
- This skill does not provide financial advice.
