---
name: prophet-market-seeder
description: "Take referred Prophet users from setup through bounded market creation with referral-aware auth checks, candidate scoring, filtered submission, and clear run reporting."
---

# Prophet Market Seeder

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

## When to Use

- create Prophet markets from an affiliate referral flow
- set up Prophet market seeding
- run Prophet market candidate generation and submission
- check Prophet market seeder status

## Workflow Summary

1. `normalize_request` uses `transform.normalize_request`
2. `validate_referral_context` uses `transform.validate_referral_context`
3. `validate_prophet_access` uses `transform.validate_prophet_access`
4. `connect_storage` uses `connector.storage.connect`
5. `load_recent_context` uses `connector.storage.query`
6. `generate_candidates` uses `transform.generate_market_candidates`
7. `score_candidates` uses `transform.score_market_candidates`
8. `filter_candidates` uses `transform.filter_market_candidates`
9. `submit_candidates` uses `transform.submit_market_batch`
10. `persist_run` uses `connector.storage.upsert`
11. `render_summary` uses `transform.render_report`
