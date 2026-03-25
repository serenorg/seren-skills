---
name: prophet-growth-agent
description: "Reinforce repeat Prophet market creation with lightweight status checks, progress tracking, reminder copy, and re-engagement recommendations after first success."
---

# Prophet Growth Agent

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

## When to Use

- check Prophet growth agent status
- generate Prophet re-engagement reminders
- track progress toward repeated Prophet market creation
- run Prophet growth follow-up

## Workflow Summary

1. `normalize_request` uses `transform.normalize_request`
2. `validate_prophet_access` uses `transform.validate_prophet_access`
3. `connect_storage` uses `connector.storage.connect`
4. `load_recent_activity` uses `connector.storage.query`
5. `compute_progress` uses `transform.compute_repeat_creation_progress`
6. `generate_checkin_actions` uses `transform.generate_checkin_actions`
7. `compose_reminder_copy` uses `transform.compose_reminder_copy`
8. `persist_growth_outputs` uses `connector.storage.upsert`
9. `render_summary` uses `transform.render_report`

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
3. Applies the `prophet_growth_agent` schema and required tables.
4. Validates the Prophet session token against the live `ViewerWalletBalance` GraphQL query.

If `SEREN_API_KEY` is missing, the runtime does not pause for DB setup questions. It fails immediately with a setup message that points the user to `https://docs.serendb.com/skills.md`.

## Testnet Mode

To run against Prophet Testnet instead of production, enable testnet in your config:

```json
{
  "testnet": {
    "enabled": true,
    "base_url": "https://testnet.prophetmarket.ai",
    "usdc_faucet": "0xa0f2da5e260486895d73086dd98af09c25dc2883c6ac96025a688f855c180d06"
  }
}
```

Or set the environment variable:

```bash
export PROPHET_TESTNET_MODE=true
```

The USDC faucet contract at `0xa0f2da5e260486895d73086dd98af09c25dc2883c6ac96025a688f855c180d06` can be used to mint fake USDC for testnet wallets. When testnet mode is active, the run output includes a `testnet` block with the faucet address and testnet base URL.

## Minimal Run

```bash
cd prophet/prophet-growth-agent
python3 -m pip install -r requirements.txt
cp config.example.json config.json
export SEREN_API_KEY=...
export PROPHET_SESSION_TOKEN='eyJ...'
python3 scripts/agent.py --config config.json
```
