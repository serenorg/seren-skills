---
name: prophet-polymarket-edge
display-name: "Prophet Polymarket Edge"
description: "Loss-protection check for retail Polymarket and Prophet users. Surfaces markets where Polymarket has drifted from cross-platform consensus, plus a watchlist of long-tail markets not yet on Prophet. Read-only by default; opt-in `--yes-live` takes the consensus side via py-clob-client when both trading-safety gates pass."
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

- it does not move money, ask for a wallet, or read your Polymarket trade history
- it does not tell you which side of the market to take in the read-only output — the cross-platform consensus number is shown as background context, not a recommendation
- it does not place orders unless you opt in with `--yes-live` AND both trading-safety gates pass; the read-only default never requires `POLY_*` credentials

When you do opt in to live trading with `--yes-live`, the skill takes the cross-platform consensus side on Polymarket for each Surface C row whose divergence is above the configured floor, sized by Kelly fraction and capped by `max_position_notional_usd` — see *Live trading* below.

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
- `--yes-live` — opt in to live trading. The skill runs both trading-safety gates first; if either fails, it exits with code 2 and prints a structured `trading_safety_blocked` payload on stderr listing every gate the request failed. If both pass, the skill plans BUY orders on the cross-platform consensus side for each Surface C row above the divergence floor and submits them via `py-clob-client`. See *Live trading* below.

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

## Live trading (`--yes-live`)

`--yes-live` is opt-in. Every run starts read-only; live trading only happens when you pass the flag AND both trading-safety gates pass.

When you pass `--yes-live`, the runtime calls `evaluate_trading_safety_gates(config)`. If either gate fails, the process exits with code 2 and emits a structured `trading_safety_blocked` JSON payload on stderr listing every gate that failed, with an `error_code`, a `missing` list, and a human-readable `message`. **No order is submitted.**

The two gates:

1. **Risk-framework gate** (`check_risk_framework_gate`) — requires the config to carry a Kelly fraction in `(0, 0.10]`, a midpoint safe band inside `[0.30, 0.70]`, a 24-hour volume floor of at least `$5,000`, a resolution-buffer window of at least 14 days, an inventory hold-cycle limit of at least 1, and a positive position cap. Error codes: `risk_framework_missing`, `risk_framework_unsafe`.
2. **Execution-path gate** (`check_execution_path_gate`) — requires `py_clob_client` to be importable AND `POLY_PRIVATE_KEY` (or `WALLET_PRIVATE_KEY`) AND `POLY_API_KEY` AND `POLY_PASSPHRASE` AND `POLY_SECRET` to be present in the process environment. Error codes: `clob_client_missing`, `poly_credentials_missing`.

When both gates pass, the runtime fetches the same Surface C rows the read-only path renders, then for each row with `consensus_direction` set and `divergence_bps` above `config.live.min_divergence_bps`:

1. Computes the Kelly fraction for the consensus side at the current Polymarket price.
2. Clips that fraction at `risk.max_kelly_fraction` and converts to a notional using `config.live.bankroll_usd`.
3. Caps notional at `risk.max_position_notional_usd`.
4. Submits a BUY at the consensus side via `py_clob_client` (`DirectClobTrader.create_order`, lifted from `polymarket/maker-rebate-bot/scripts/polymarket_live.py:2049-2187`).

Markets with no `consensus_direction`, no `polymarket_token_id`, or non-positive Kelly edge are skipped silently. The final stdout payload is a JSON object containing `status`, `trading_safety` (the evaluated gates), `plans` (planned orders), and `live_executions` (broker responses).

### Required `live` config block

```json
{
  "live": {
    "bankroll_usd": 1000.0,
    "min_divergence_bps": 500
  }
}
```

### Emergency exit

There is no `--unwind-all` or stop-trading subcommand wired into this skill. The Kelly + per-market notional caps bound single-trade exposure, but if you need to liquidate held positions before resolution, use the maker-rebate-bot emergency-exit path or close manually on Polymarket.

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
- automated unwind / cancel-all / position monitoring on the live path
