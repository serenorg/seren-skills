---
name: hybrid-signal-trader
description: "Score Kalshi contract opportunities with cross-venue divergence plus gap and coil support, then return dry-run trade intents with plain-language rationale."
---

# Kalshi Hybrid Signal Trader

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

## When to Use

- find kalshi trades
- rank kalshi opportunities
- explain a kalshi contract with macro support

## On Invoke

**Immediately run the default Kalshi scan without asking.** Do not present a menu. Execute:

```bash
cd ~/.config/seren/skills/kalshi-hybrid-signal-trader && python3 scripts/agent.py --config config.json --mode scan
```

If the user explicitly requests `paper` or `live`, run that mode instead. After results are displayed, present the next steps.

## Output Contract

This skill is self-contained. Use these local references:

- `references/output-contract.md`
- `references/desktop-summary-template.md`

Human-facing output must be rendered first as a `mini research note`. Structured agent payload comes immediately after it using `kalshi-shared-v1`.

## Workflow Summary

1. `fetch_kalshi_markets` uses `connector.kalshi_oracle.get`
2. `fetch_gap_signals` uses `connector.macro_gap.get`
3. `fetch_coil_signals` uses `connector.macro_coil.get`
4. `assess_signal_health` uses `transform.assess_kalshi_signal_health`
5. `score_market_candidates` uses `transform.score_kalshi_hybrid_candidates`
6. `rank_candidates` uses `transform.rank_kalshi_candidates`
7. `create_trade_intents` uses `transform.create_kalshi_trade_intents`
8. `persist_run` uses `connector.storage.post`
9. `persist_candidates` uses `connector.storage.post`
10. `persist_trade_decisions` uses `connector.storage.post`
11. `render_summary` uses `transform.render_kalshi_hybrid_report`

## Execution Modes

- `scan` (default): return ranked dry-run trade intents plus watchlist and health summary.
- `paper`: same selection logic, but mark the selected trades as paper-only records suitable for persistence or downstream automation.
- `live`: requires explicit `--yes-live` and still fails closed if signal health is ambiguous. This runtime emits trade intent output only; it does not submit broker orders.

## Product Behavior

The trader combines:

- Kalshi oracle divergence
- `gap` support
- `coil` support
- freshness and health interpretation
- plain-language rationale

If `gap` or `coil` is ambiguous, downgrade the candidate to watchlist-only and explain the downgrade explicitly.

## Human-Facing Output

For every surfaced contract, explain:

- what the Kalshi contract is
- what divergence or consensus break was observed
- what `gap` saw
- what `coil` saw
- whether it is tradable or watchlist-only
- what key risk could make the view wrong
- whether freshness or health caveats are present

## Shared Output Fields

This skill must return:

- `run_status`
- `mode`
- `generated_at`
- `signal_health`
- `market_candidates`
- `selected_trades`
- `watchlist`
- `blocked_reasons`
- `rationale`
- `risk_note`
- `freshness`
- `desktop_summary`
- `audit`

## Trade Execution Contract

When the user gives a direct exit instruction such as `sell`, `close`, `exit`, `unwind`, or `flatten`, treat it as an instruction to cancel or clear any planned Kalshi trade intents immediately. Ask only the minimum clarifying question if the target contract is ambiguous.

## Pre-Trade Checklist

Before marking a contract as live-eligible:

1. Fetch the current Kalshi oracle payload and confirm the contract is still active.
2. Check `gap` and `coil` freshness and interpret empty responses explicitly.
3. Verify required publishers are reachable and `SEREN_API_KEY` is loaded.
4. If signal health is ambiguous, fail closed and downgrade to watchlist-only.
5. Return a customer-readable explanation of the downgrade, not just an empty trade list.

## Dependency Validation

The runtime must verify required credentials and publishers before continuing:

- `SEREN_API_KEY`
- `seren-polymarket-intelligence`
- `serendb`

If anything is missing, stop with a remediation message instead of guessing or attempting live execution.

## Persistence

Persist at minimum:

- scan runs
- ranked Kalshi candidates
- selected trade intents
- watchlist entries
- signal health snapshots
- rationale payloads

## Safety Notes

- Default mode is non-live.
- `live` requires `--yes-live`.
- Ambiguous `gap` or `coil` state blocks live eligibility.
- If no trade qualifies, return a ranked watchlist and explain why no trades passed.
