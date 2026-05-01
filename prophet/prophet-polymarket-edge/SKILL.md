---
name: prophet-polymarket-edge
display-name: "Prophet Polymarket Edge"
description: "Read-only Tranche 1 watchlist of Prophet markets to create plus Polymarket consensus context, anchored to cross-platform divergence and the Prophet open-market list. Surface A loss audit is post-v1; this skill ships Surface B (watchlist) and Surface C (consensus context) only."
---

# Prophet Polymarket Edge

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

## V1 Scope (May 1, 2026 launch)

This skill ships exactly two surfaces at v1:

- **Surface B — Tranche 1 watchlist (read-only).** Ranked list of candidate markets to create on Prophet, drawn from `(Polymarket divergent + consensus markets) − (Prophet open markets)`. The watchlist itself renders unauthenticated. The "Create this market on Prophet" deep link requires Prophet auth via Privy email-OTP; if the user declines OTP, the deep link is suppressed for the rest of the run.
- **Surface C — Polymarket consensus context (read-only).** For each candidate Polymarket market, render Polymarket URL, current Polymarket price, consensus probability, consensus direction, divergence in bps, and a freshness note.

**Out of scope at v1.** Surface A (loss audit), Polymarket trade history pull, wallet/email entry, pattern detection, personalized Prophet handoff with deep links, Stage v1.1 pricing-divergence observation feed, and `/api/oracle/actionable` are all deferred. See the design doc §11 / §13.9 for the launch contingency framing.

**Execution is disabled at v1.** Surface C does not accept `--yes-live`. `POLY_*` credentials are not solicited. The skill prints read-only consensus context and exits.

## When to Use

- run a Prophet Tranche 1 watchlist (May 1–8 launch window)
- show Polymarket consensus context for divergent markets
- find markets present on Polymarket but not yet on Prophet
- check the Polymarket vs cross-platform consensus on markets the user follows

## Workflow Summary

1. `bootstrap_storage` — resolve/create Seren project `prophet-polymarket-edge`, database `prophet_polymarket_edge`, apply idempotent DDL from `serendb_schema.sql` (§10.1, §10.2).
2. `fetch_prophet_open_markets` — pull current open Prophet markets via GraphQL.
3. `fetch_polymarket_divergence` — call `seren-polymarket-intelligence` `/api/oracle/divergence` with `min_platforms=3`, `min_liquidity_usd=10000`.
4. `fetch_polymarket_consensus_batch` — call `/api/oracle/consensus/batch` for the divergent candidate set.
5. `compute_watchlist_candidates` — set difference: candidates on Polymarket / consensus venues but not on Prophet's open list. Rank by consensus confidence, divergence magnitude, liquidity floor.
6. `render_watchlist` — top 5 with verbatim consensus-context block per §6.1, optional deep link gated on Prophet auth.
7. `render_consensus_context` — Surface C read-only Polymarket consensus per market: URL, current Polymarket price, consensus probability, consensus direction, divergence bps, freshness note.
8. `persist_recommendations` — write rows to `recommendations` with `recommendation_id`.

## Verbatim Renderer Copy

### §6.1 Consensus context block (per watchlist row)

Rendered visually below each market description, never co-equal with a price target.

```text
Cross-platform consensus context, where available.
This is not Prophet's quote, not a trading signal, and not a claim
that the AI House will price above or below it. Use it only as
background context when deciding whether the market is worth creating.
```

Surface C must use the labels **`consensus probability`** and **`consensus direction`** — never `recommended side`.

## Auth Contract

- **`SEREN_API_KEY`** is required. Without it, the runtime exits immediately with a setup-message pointing to `https://docs.serendb.com/skills.md`.
- **`PROPHET_SESSION_TOKEN`** is optional. Without it, Surface B renders the watchlist read-only and suppresses all "Create this market on Prophet" deep links. Surface C is unaffected by Prophet auth.
- The Prophet token, if provided, is acquired via the same Privy email-OTP flow used by `prophet-market-seeder`. The token is a JWT starting with `eyJ...` and expires after ~1 hour.
- **`POLY_*` credentials are not solicited at v1 launch.** Surface C is read-only.

## First-Run Setup

Storage bootstrap runs on every invoke before any read or write:

1. Resolve or create Seren project `prophet-polymarket-edge`.
2. Resolve or create database `prophet_polymarket_edge`.
3. Apply the idempotent DDL from `serendb_schema.sql`.
4. If `SEREN_API_KEY` is missing, the runtime fails immediately with a setup message pointing to `https://docs.serendb.com/skills.md`. It does not pause for setup questions.

## Privacy & Retention

- Wallet input is **never** stored verbatim. Only `source_input_hash` (salted SHA-256) and `source_input_redacted` (display-only) are persisted (§10.4 / §13.19).
- Default retention for audit content is 180 days.
- The `--purge` flag removes audit/findings/recommendations rows for the user.

## Minimal Run

```bash
cd prophet/prophet-polymarket-edge
python3 -m pip install -r requirements.txt
cp config.example.json config.json
export SEREN_API_KEY=...
# Optional — only needed for Surface B deep links:
export PROPHET_SESSION_TOKEN='eyJ...'
python3 scripts/agent.py --config config.json
```

## Out of Scope (post-v1)

The following are explicitly deferred. Each is tracked as follow-up work against the launch issue:

- Surface A loss audit (needs Phase 0 data-api spike + RPC fallback + 50-wallet match coverage).
- Polymarket trade-history pull and pattern detection.
- Wallet/email entry paths.
- Personalized Prophet handoff with deep links anchored to user trades.
- Stage v1.1 pricing-divergence observation feed (gated on n≥30/category AND ≥7 days of AI House quotes).
- `/api/oracle/actionable` (recommendation engine, post-v1 legal review required per §13.14).
- Polymarket CLOB execution path. Re-enabling requires a documented jurisdictional eligibility attestation, technical CLOB preflight, and legal/advice review.
