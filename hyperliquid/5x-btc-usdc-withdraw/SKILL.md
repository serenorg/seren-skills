---
name: 5x-btc-usdc-withdraw
display-name: "Hyperliquid 5x BTC Withdraw"
description: "5x leveraged BTC-PERP on Hyperliquid with free USDC withdrawal. No KYC, no debt, self-custody. Dry-run includes 270-day liquidation risk backtest via CoinGecko."
---

# Hyperliquid 5x BTC USDC Withdraw

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

## When to Use

- open 5x bitcoin leverage on hyperliquid and withdraw usdc
- hyperliquid 5x btc perp with usdc withdrawal
- leverage btc on hyperliquid and withdraw free usdc

## What This Skill Does

Deposits USDC to Hyperliquid, opens a 5x BTC-PERP long, and withdraws the free USDC (deposit minus margin). No debt — the withdrawn USDC is yours. All Hyperliquid API calls route through the `seren-hyperliquid` publisher (HyperEVM + HyperCore via QuickNode). Backtest uses `coingecko-serenai` publisher for BTC prices.

### Pipeline

```text
USDC (any CCTP-enabled chain)
 │
 ▼ ① CCTP deposit USDC to Hyperliquid
USDC on Hyperliquid
 │
 ▼ ② Open 5x BTC-PERP (20% margin locked)
 │
 ▼ ③ Withdraw free USDC via CCTP (to any chain)
 │
 ▼ Done — long BTC 5x + free USDC in your wallet
```

### Example ($200 deposit)

| Deposit | Margin (5x) | BTC Position | Free USDC | Withdrawal Fee | **You Get** |
| --- | --- | --- | --- | --- | --- |
| $200 | $40 | $200 notional | $160 | ~$1 | **$159** |
| $100 | $20 | $100 notional | $80 | ~$1 | **$79** |
| $50 | $10 | $50 notional | $40 | ~$1 | **$39** |

**If liquidated**: you lose the margin, keep the withdrawn USDC. Net positive.

## Trade Execution Contract

The words **exit**, **close**, **unwind**, and **stop** are immediate operator instructions. Close the BTC-PERP immediately.

## Pre-Trade Checklist

1. Verify `HYPERLIQUID_PRIVATE_KEY` is set
2. Verify `SEREN_API_KEY` is set
3. Confirm USDC balance on Hyperliquid
4. Verify BTC-PERP market is available
5. Run 270-day liquidation risk backtest and display results
6. Fail-closed if any check fails

## Live Safety Opt-In

**Default mode is `dry-run`.** Runs full simulation including 270-day liquidation backtest. No positions opened.

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

## Immediately Run

When this skill is invoked, immediately run:

```bash
python scripts/agent.py --config config.json
```

## Liquidation Risk Backtest

Dry-run fetches 270 days of BTC prices from CoinGecko (via Seren `coingecko-serenai` publisher) and simulates liquidation risk:

| Leverage | Liq Threshold | Liq Rate (270d) | Risk |
| --- | --- | --- | --- |
| 5x | 17% drop | ~20% | HIGH |
| 10x | 7% drop | ~44% | VERY HIGH |
| 50x | 1% drop | ~89% | VERY HIGH |

## Environment Variables

| Variable | Required | Description |
| --- | --- | --- |
| `HYPERLIQUID_PRIVATE_KEY` | Yes | Wallet private key for Hyperliquid EIP-712 signing |
| `SEREN_API_KEY` | Yes | Seren API key — CoinGecko publisher for backtest |

## Cost Breakdown

| Component | Estimated Cost |
| --- | --- |
| Hyperliquid trading fee | 0.035% (taker) |
| CCTP withdrawal | ~$1 USDC |
| **Total** | **~$1 on any deposit** |

## Risks and Disclaimers

- **Liquidation risk**: At 5x, ~17% BTC drop triggers liquidation. You lose margin but keep withdrawn USDC.
- **No debt**: Nothing is borrowed. Withdrawn USDC is free.
- This skill does not provide financial advice.
