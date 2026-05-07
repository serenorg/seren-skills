---
name: prophet-bounty-runner
description: "Run the Prophet Polymarket-Mirror Sprint bounty workflow — auto-OTP login, generate and submit Prophet markets, post proof to seren-bounty, and report earnings status."
---

# Prophet Bounty Runner

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

## When to Use

- earn the prophet sprint bounty
- run the prophet bounty runner
- check my prophet bounty status
- mirror settling polymarket markets to prophet for the bounty

## Workflow Summary

1. `normalize_request` uses `transform.normalize_request`
2. `validate_seren_auth` uses `transform.validate_seren_auth`
3. `resolve_bounty` uses `transform.resolve_and_validate_prophet_bounty`
4. `ensure_bounty_join` uses `connector.bounty.post`
5. `acquire_prophet_token` uses `transform.acquire_prophet_token_via_otp`
6. `bind_participant_identity` uses `transform.bind_participant_identity`
7. `discover_polymarket_sources` uses `transform.discover_polymarket_sources`
8. `generate_candidates` uses `transform.generate_market_candidates`
9. `score_candidates` uses `transform.score_market_candidates`
10. `filter_candidates` uses `transform.filter_market_candidates`
11. `dedup_against_prophet` uses `transform.dedup_candidates_against_prophet`
12. `submit_markets_to_prophet` uses `transform.submit_market_batch`
13. `enforce_eligibility_gates` uses `transform.enforce_eligibility_gates`
14. `record_proofs_to_bounty` uses `connector.bounty.post`
15. `fetch_earnings_status` uses `connector.bounty.get`
16. `persist_run` uses `connector.storage.upsert`
17. `render_summary` uses `transform.render_report`
