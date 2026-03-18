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

## Auth Contract

The skill acquires the Prophet session token automatically via Playwright using the email OTP flow:

1. Navigate to `https://app.prophetmarket.ai`
2. Click the "Connect" button to open the Privy auth modal
3. Check `localStorage["privy:token"]` — if already set, use it directly
4. If not authenticated:
   a. Prompt user for their Prophet email (or read from config `inputs.prophet_email`)
   b. Fill `#email-input` and click `button:has-text("Submit")`
   c. Privy sends a 6-digit OTP to the user's email
   d. Prompt user for the 6-digit code
   e. Fill `input[name="code-0"]` through `input[name="code-5"]`
   f. Poll `localStorage["privy:token"]` until non-null (with 60s timeout)
5. Extract the JWT and pass it as `PROPHET_SESSION_TOKEN`

**Important:**
- Always use the email OTP path (wallet connect and Google OAuth do not work in Playwright)
- The token is a JWT starting with `eyJ...` and expires after ~1 hour
- The `privy-session` cookie alone is not sufficient for authenticated GraphQL access

## First-Run Setup

The runtime now auto-bootstraps Prophet storage on first run:

1. Resolves or creates the Seren project `prophet`.
2. Resolves or creates the Seren database `prophet`.
3. Applies the `prophet_market_seeder` schema and required tables.
4. Validates the Prophet session token against the live `ViewerWalletBalance` GraphQL query.

If `SEREN_API_KEY` is missing, the runtime does not pause for DB setup questions. It fails immediately with a setup message that points the user to `https://docs.serendb.com/skills.md`.

## Minimal Run

```bash
cd prophet/prophet-market-seeder
python3 -m pip install -r requirements.txt
cp config.example.json config.json
export SEREN_API_KEY=...
export PROPHET_SESSION_TOKEN='eyJ...'
python3 scripts/agent.py --config config.json
```
