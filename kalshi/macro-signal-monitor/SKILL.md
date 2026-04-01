---
name: macro-signal-monitor
description: "Monitor gap and coil alignment for Kalshi contracts, map macro support to candidate markets, and explain signal freshness or ambiguity."
---

# Kalshi Macro Signal Monitor

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

## When to Use

- monitor kalshi macro signals
- show gap and coil support for kalshi
- what changed in kalshi macro alignment

## Output Contract

This skill is self-contained. Use these local references:

- `references/output-contract.md`
- `references/desktop-summary-template.md`

## Workflow Summary

1. `fetch_kalshi_markets` uses `connector.kalshi_oracle.get`
2. `fetch_gap_signals` uses `connector.macro_gap.get`
3. `fetch_coil_signals` uses `connector.macro_coil.get`
4. `assess_signal_health` uses `transform.assess_kalshi_signal_health`
5. `map_macro_signals` uses `transform.map_macro_signals_to_kalshi_contracts`
6. `rank_macro_alignment` uses `transform.rank_kalshi_macro_alignment`
7. `compare_previous_scan` uses `transform.diff_kalshi_macro_runs`
8. `persist_run` uses `connector.storage.post`
9. `persist_snapshot` uses `connector.storage.post`
10. `render_summary` uses `transform.render_kalshi_macro_report`

## Responsibilities

- monitor `gap` and `coil` support relevant to Kalshi contracts
- map macro signals to candidate contracts
- expose directional alignment and non-alignment
- show what changed since the prior scan
- persist macro signal monitor snapshots

## Output Rules

Explain:

- what `gap` saw
- what `coil` saw
- whether they aligned or conflicted
- whether the signal is fresh enough to trust
- whether the contract should stay watchlist-only because health is unclear
