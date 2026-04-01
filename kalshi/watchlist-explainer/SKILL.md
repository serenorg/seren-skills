---
name: watchlist-explainer
description: "Explain why a Kalshi contract is interesting but not yet tradable, with freshness caveats, near-miss logic, and plain-language watchlist guidance."
---

# Kalshi Watchlist Explainer

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

## When to Use

- explain why this kalshi contract is watchlist only
- show near miss kalshi setups
- build a kalshi watchlist

## Shared Contract

Use the same Kalshi suite response contract and desktop summary template:

- `kalshi/_shared/output-contract.md`
- `kalshi/_shared/desktop-summary-template.md`

## Workflow Summary

1. `fetch_kalshi_markets` uses `connector.kalshi_oracle.get`
2. `fetch_gap_signals` uses `connector.macro_gap.get`
3. `fetch_coil_signals` uses `connector.macro_coil.get`
4. `assess_signal_health` uses `transform.assess_kalshi_signal_health`
5. `classify_watchlist_candidates` uses `transform.classify_kalshi_watchlist_candidates`
6. `rank_watchlist` uses `transform.rank_kalshi_watchlist`
7. `persist_run` uses `connector.storage.post`
8. `persist_watchlist` uses `connector.storage.post`
9. `render_summary` uses `transform.render_kalshi_watchlist_report`

## Responsibilities

- surface near-miss Kalshi contracts
- explain why a contract is interesting but not actionable yet
- distinguish single-signal confirmation from weak conviction
- show freshness and health caveats clearly
- persist ranked watchlist snapshots

## Output Rules

Every watchlist item should explain:

- the contract
- what divergence was observed
- what `gap` saw
- what `coil` saw
- why it stayed watchlist-only
- the key risk
- the next check

If no contract qualifies for the watchlist, return a health summary and explain why the list is empty.
