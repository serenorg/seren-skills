# Kalshi Shared Output Contract

Version: `kalshi-shared-v1`

Every Kalshi skill in this suite returns the same top-level fields so the result works for both:

- a human reading the response in Seren Desktop
- an agent continuing the workflow programmatically

## Human-Facing Summary

Default style: `mini_research_note`

Render the human-facing summary first, in this order:

1. `verdict`  
   One sentence. State whether the best contract is tradable, watchlist-only, or blocked.
2. `what_happened`  
   Two to four plain-English sentences explaining the divergence or macro change.
3. `why_it_matters`  
   Explain what `gap` saw, what `coil` saw, and how that changed conviction.
4. `main_risk`  
   Name the single most important way the view could be wrong.
5. `next_action`  
   Tell the user whether to paper trade, watchlist, or wait for confirmation.

The full human summary should be readable in under 30 seconds and avoid unexplained jargon.

## Structured Payload

Every skill returns:

```json
{
  "run_status": "ok | blocked | degraded",
  "mode": "scan | paper | live | watchlist | monitor | report",
  "generated_at": "RFC3339 timestamp",
  "signal_health": {
    "overall": "healthy | ambiguous | degraded",
    "gap": "healthy | empty | stale | degraded",
    "coil": "healthy | empty | stale | degraded",
    "interpretation": "string"
  },
  "market_candidates": [],
  "selected_trades": [],
  "watchlist": [],
  "blocked_reasons": [],
  "rationale": [],
  "risk_note": "string",
  "freshness": {
    "kalshi_oracle": "RFC3339 timestamp or unknown",
    "gap": "RFC3339 timestamp or unknown",
    "coil": "RFC3339 timestamp or unknown"
  },
  "desktop_summary": {
    "style": "mini_research_note",
    "verdict": "string",
    "what_happened": "string",
    "why_it_matters": "string",
    "main_risk": "string",
    "next_action": "string"
  },
  "audit": {
    "suite": "kalshi_signal_suite",
    "contract_version": "kalshi-shared-v1",
    "skill": "skill slug",
    "run_id": "stable local or persisted identifier"
  }
}
```

## Required Candidate Shape

Each item in `market_candidates`, `selected_trades`, and `watchlist` should include:

- `contract`
- `title`
- `state`
- `divergence_bps`
- `gap_view`
- `coil_view`
- `health_caveat`
- `thesis`
- `key_risk`
- `next_check`

## Safety Rules

- If `gap` or `coil` is ambiguous, `selected_trades` must be empty in live-sensitive modes.
- If no trade qualifies, return a ranked `watchlist` instead of an empty response.
- `risk_note` is mandatory even when there are no trades.
- `blocked_reasons` must explain why a contract is downgraded from tradable to watchlist-only.
