---
name: prophet-polymarket-edge
display-name: "Prophet Polymarket Edge"
description: "Read-only loss-protection check for retail Polymarket and Prophet users. Surfaces markets where Polymarket prices have drifted from cross-platform consensus, plus a watchlist of long-tail markets that aren't on Prophet yet. The skill never places a trade."
---

# Prophet Polymarket Edge

A read-only second opinion for anyone trading on Polymarket or thinking about creating a market on Prophet.

## Why this skill exists

Retail traders consistently lose money on Polymarket. The pattern is well documented across the 2024 US election cycle and beyond: small accounts pile into a market late, after the price has already moved, in a contract that is too thin to exit cleanly. When the outcome resolves the wrong way, those late buyers are the ones holding the loss. Long-tail markets with low volume make this worse, because there is no orderbook depth to sell into when sentiment shifts.

This skill is a response to that loss pattern. It does not promise alpha and it does not place trades. It runs three plain checks before you put money down:

1. **Is Polymarket pricing this market the same way the rest of the prediction-market world is?** If Polymarket is far apart from the Kalshi / Manifold / Metaculus / PredictIt cross-platform consensus, that gap is information. It might mean Polymarket has the right read — or it might mean retail flow has pushed the price somewhere the rest of the world disagrees with. Either way, you should know before you buy.
2. **Is the market liquid enough to exit if you're wrong?** The skill enforces a minimum 24-hour volume floor before a market shows up in either output, so you don't see candidates you can't get out of.
3. **Is there a Prophet equivalent you should be creating instead of buying Polymarket?** If a divergent, liquid Polymarket contract has no Prophet open-market match, the skill puts it on a watchlist with a "create this on Prophet" suggestion (and a deep link, if you've signed in to Prophet).

What you get back is two text blocks: a ranked watchlist of markets to consider creating on Prophet, and a read-only context table for each Polymarket market showing where it is priced today versus where the cross-platform consensus puts it. You can use either one to skip a trade you were about to lose money on.

## When to use this skill

- you are about to buy a Polymarket contract and want a sanity check from cross-platform data first
- you saw a tweet or headline pushing a Polymarket position and want to see if other prediction venues agree
- you create markets on Prophet and want a list of well-traded events that aren't listed yet
- you want to know which Polymarket long-tails are most out-of-line with the rest of the prediction-market world today

## What this skill does not do

- it does not place orders on Polymarket, on Prophet, or anywhere else
- it does not move money, ask for a wallet, or read your Polymarket trade history
- it does not tell you which side of the market to take — it shows you cross-platform consensus as background context, not as a recommendation
- it does not solicit Polymarket API keys or private keys at this version; the `POLY_*` environment variables are intentionally not requested

If a future version ever wants to actually place trades, it has to clear three machine-checked safety gates first (described below). Today every gate trips closed by design.

## What you see when you run it

Two output blocks, in this order:

**Watchlist** — top 5 (configurable up to 25) markets that are live and divergent on Polymarket but not yet open on Prophet. Each row shows the market description, why it was listed (how many other prediction venues are pricing the contract, the divergence in basis points, the Polymarket liquidity), the cross-platform consensus probability and direction, and the Polymarket price. If you provided a Prophet session token, each row also gets a "Create this market on Prophet" deep link. If you did not, the deep link is suppressed for the whole run and the rest of the body still renders.

**Polymarket consensus context** — top 10 (configurable up to 25) divergent Polymarket markets, each with the Polymarket URL, the current Polymarket price, the cross-platform consensus probability and direction, the divergence in basis points, and a freshness note. This is the read-only second opinion you use before clicking buy on Polymarket.

Both blocks render the following fixed disclaimer above any consensus number, byte-for-byte (the runtime and tests pin this copy):

```text
Cross-platform consensus context, where available.
This is not Prophet's quote, not a trading signal, and not a claim
that the AI House will price above or below it. Use it only as
background context when deciding whether the market is worth creating.
```

The labels in the consensus output are always **`consensus probability`** and **`consensus direction`**. The runtime is hard-coded to never use phrasing like "recommended side" — that wording would imply a trade signal, and this skill does not give trade signals.

## What you need to run it

- `SEREN_API_KEY` — required. Without it, the skill exits with a setup message pointing to the Seren skills docs at `https://docs.serendb.com/skills.md`. It does not ask follow-up questions.
- `PROPHET_SESSION_TOKEN` — optional. The watchlist still renders without it; only the "Create this market on Prophet" deep links are suppressed when it's missing. The token is the same Privy email-OTP JWT used by `prophet-market-seeder` (starts with `eyJ...`, expires roughly hourly).
- Polymarket API keys — **not requested**. This version is read-only and does not solicit `POLY_*` or wallet credentials.

## How to run it

```bash
cd prophet/prophet-polymarket-edge
python3 -m pip install -r requirements.txt
cp config.example.json config.json
export SEREN_API_KEY=...
# Optional — only needed for "Create this market on Prophet" deep links:
export PROPHET_SESSION_TOKEN='eyJ...'
python3 scripts/agent.py --config config.json
```

Common flags:

- `--json` — machine-readable output instead of the rendered text blocks.
- `--purge` — wipe stored audit, finding, and recommendation rows for the current user (see Privacy below).
- `--yes-live` — **rejected**. The skill exits with code 2 and prints a structured `trading_safety_blocked` payload on stderr listing every gate the request failed. See *Trading safety gates* below.

## First-run storage bootstrap

The skill provisions its own SerenDB project and database the first time it runs:

1. Resolves or creates the Seren project `prophet-polymarket-edge`.
2. Resolves or creates the database `prophet_polymarket_edge`.
3. Applies the idempotent DDL from `serendb_schema.sql`, creating the audit, recommendation, and telemetry tables under the `prophet_polymarket_edge` schema.

If `SEREN_API_KEY` is missing the runtime fails fast — no setup prompts, no partial provisioning.

## Privacy and retention

- Wallet input is never stored verbatim. The schema only persists a salted SHA-256 hash and a redacted display string, so the underlying wallet address is not recoverable from the database.
- Default retention for audit content is 180 days.
- `python3 scripts/agent.py --config config.json --purge` removes the audit-run, audit-finding, recommendation, telemetry, and wallet-identity rows for the current user.

## Emergency exit and stop trading

There is no emergency exit path, no `--unwind-all`, no `close-all`, no `flatten`, and no stop-trading subcommand — because the skill never holds inventory, never places orders, and has no positions to unwind or liquidate. There is nothing to cancel and nothing to market-sell. `--yes-live` is rejected with exit code 2 before any execution path runs; see *Trading safety gates* below.

## Trading safety gates (why `--yes-live` is rejected today)

The skill is read-only. It does not place trades, and `--yes-live` is rejected with exit code 2. That rejection is not a placeholder — it is enforced by three machine-checked gates in `scripts/trading_safety.py`. Any future change that wants to wire `--yes-live` to a real execution path has to clear all three first; loosening these checks without simultaneously landing the corresponding mitigations is treated as a P0 defect by the existing test suite.

When `--yes-live` is passed, the runtime calls `evaluate_trading_safety_gates(config)` and emits a structured `trading_safety_blocked` JSON payload on stderr listing every gate that failed, with an `error_code`, a `missing` list, and a human-readable `message`. The process exits with code 2.

The three gates:

1. **Signal-calibration gate** (`check_signal_calibration_gate`) — fails closed unless the config carries a backtest with at least 120 events AND a strictly positive net return. Cross-platform divergence on its own is not a tradable edge until it's been validated on resolved outcomes. Error codes: `insufficient_sample_size`, `backtest_gate_blocked`.
2. **Risk-framework gate** (`check_risk_framework_gate`) — fails closed unless the config carries a Kelly fraction in `(0, 0.10]`, a midpoint safe band inside `[0.30, 0.70]`, a 24-hour volume floor of at least `$5,000`, a resolution-buffer window of at least 14 days, an inventory hold-cycle limit of at least 1, and a positive position cap. Error codes: `risk_framework_missing`, `risk_framework_unsafe`.
3. **Execution-path gate** (`check_execution_path_gate`) — fails closed unless `py_clob_client` is importable AND `POLY_PRIVATE_KEY` (or `WALLET_PRIVATE_KEY`) AND `POLY_API_KEY` AND `POLY_PASSPHRASE` AND `POLY_SECRET` are all present in the process environment. Error codes: `clob_client_missing`, `poly_credentials_missing`.

Today's defaults trip every gate closed because none of those preconditions are wired. That is intentional. The retail-loss pattern this skill is responding to is the reason: a half-finished trading path with no calibration, no risk framework, and no real CLOB execution would just add another way to lose money.

## How the read-only run works (for Claude / Codex)

The runtime executes these steps every invoke:

1. `bootstrap_storage` — resolve / create the Seren project and database, apply the schema DDL idempotently.
2. `fetch_prophet_open_markets` — pull the current open Prophet markets via Prophet GraphQL. If `PROPHET_SESSION_TOKEN` is missing, this returns an empty list and the runtime continues.
3. `fetch_polymarket_divergence` — call `seren-polymarket-intelligence` `/api/oracle/divergence` with `min_platforms=3` and `min_liquidity_usd=10000` (both configurable).
4. `fetch_polymarket_consensus_batch` — call `/api/oracle/consensus/batch` for the divergent candidate set.
5. `compute_watchlist_candidates` — keep Polymarket divergent rows whose market description does not match any open Prophet market. Rank by platform count, then divergence magnitude, then liquidity.
6. `render_watchlist` — top N candidates with the verbatim consensus context block per row, deep link gated on Prophet auth.
7. `render_consensus_context` — for each divergent Polymarket market, render the URL, current price, consensus probability, consensus direction, divergence in basis points, and a freshness note.
8. `persist_recommendations` — write rows to `audit_runs`, `recommendations`, and `telemetry_events`.

The Polymarket intelligence client deliberately does not expose the recommendation engine endpoint. The test suite asserts the route name does not appear in any executable code path, only in comments documenting why it is excluded.

## Out of scope at this version

The following are intentionally not in the runtime today. Each one is its own follow-up:

- a personal Polymarket loss audit driven by your trade history
- pulling your Polymarket trade history at all, or asking for a wallet / email
- pattern detection across your trades
- a personalized Prophet handoff with deep links anchored to your prior trades
- a pricing-divergence observation feed gated on AI House quote history
- the Polymarket recommendation engine endpoint
- a Polymarket CLOB execution path of any kind
