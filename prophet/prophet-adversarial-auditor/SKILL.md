---
name: prophet-adversarial-auditor
display-name: "Prophet Adversarial Auditor"
description: "Inspect Prophet market creation history for rejected submissions, replayable failures, suspicious patterns, and plausible economic loss scenarios with structured findings for operators."
---

# Prophet Adversarial Auditor

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

## When to Use

- audit Prophet market creation failures
- review Prophet rejected submissions
- inspect Prophet bugs and loss scenarios
- check Prophet auditor status

## Workflow Summary

1. `normalize_request` uses `transform.normalize_request`
2. `validate_prophet_access` uses `transform.validate_prophet_access`
3. `connect_storage` uses `connector.storage.connect`
4. `load_run_history` uses `connector.storage.query`
5. `replay_recent_runs` uses `transform.replay_recent_runs`
6. `detect_findings` uses `transform.detect_audit_findings`
7. `analyze_loss_scenarios` uses `transform.analyze_loss_hypotheses`
8. `rank_findings` uses `transform.rank_findings`
9. `persist_audit_outputs` uses `connector.storage.upsert`
10. `render_summary` uses `transform.render_report`

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
3. Applies the `prophet_adversarial_auditor` schema and required tables.
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
cd prophet/prophet-adversarial-auditor
python3 -m pip install -r requirements.txt
cp config.example.json config.json
export SEREN_API_KEY=...
export PROPHET_SESSION_TOKEN='eyJ...'
python3 scripts/agent.py --config config.json
```
