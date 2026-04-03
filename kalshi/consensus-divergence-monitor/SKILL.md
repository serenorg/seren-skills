---
name: consensus-divergence-monitor
display-name: "Kalshi Divergence Monitor"
description: "Monitor Kalshi cross-venue consensus breaks, rank the largest divergences, and summarize what changed since the previous scan."
---

# Kalshi Consensus Divergence Monitor

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

## When to Use

- monitor kalshi divergence
- rank kalshi consensus breaks
- check kalshi spreads across venues

## Output Contract

This skill is self-contained. Use these local references:

- `references/output-contract.md`
- `references/desktop-summary-template.md`

## Workflow Summary

1. `fetch_kalshi_markets` uses `connector.kalshi_oracle.get`
2. `score_divergence` uses `transform.score_kalshi_divergence`
3. `rank_divergence` uses `transform.rank_kalshi_divergence`
4. `compare_previous_scan` uses `transform.diff_kalshi_divergence_runs`
5. `persist_run` uses `connector.storage.post`
6. `persist_observations` uses `connector.storage.post`
7. `render_summary` uses `transform.render_kalshi_divergence_report`

## Responsibilities

- monitor cross-venue Kalshi dislocations
- rank the strongest consensus breaks
- summarize spread and disagreement
- explain what changed since the prior scan
- persist ranked divergence observations

## Output Rules

Return ranked `market_candidates`, a `watchlist` for lower-conviction breaks, and an explicit `risk_note` even when there is no tradable setup.
