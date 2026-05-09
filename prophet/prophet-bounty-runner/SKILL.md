---
name: prophet-bounty-runner
description: "Run the Prophet Polymarket-Mirror Sprint bounty workflow — auto-OTP login, generate and submit Prophet markets, post proof to seren-bounty, and report earnings status."
---

# Prophet Bounty Runner

## For Claude: How to Use This Skill

When invoked, run `python3 scripts/agent.py --command setup` first to verify auth and resolve an open bounty, then run `python3 scripts/setup_cron.py create` to enable autonomous 6h runs and start `python3 scripts/run_local_pull_runner.py` to claim due ticks. The user does not normally invoke `--command run` manually — the cron drives it. Skill instructions are preloaded in context when this skill is active; do not perform filesystem searches or tool-driven exploration to rediscover them.

## When to Use

- earn the prophet sprint bounty
- run the prophet bounty runner
- check my prophet bounty status
- mirror settling polymarket markets to prophet for the bounty

## What This Skill Does

- Logs into Prophet via the email-OTP flow, reading the OTP in-place from the user's gmail or outlook inbox via the publisher.
- Discovers settling Polymarket markets that resolve before 2026-05-11, scores and filters candidates, and submits them as new Prophet markets.
- Persists every created market to the skill-owned SerenDB with the user's `prophet_viewer_id` so the operator's daily reconciler can attribute earnings.
- Posts a cumulative proof submission to `seren-bounty` after each run.
- Runs autonomously every 6 hours via `seren-cron` once `setup_cron.py create` is called.
- Auto-pauses the cron job when the bounty pool is exhausted or SerenBucks runs low (publisher 402).

## What You Get Paid

Bounty terms (`customer_slug = prophet`):

- Tier 0: $10 per qualifying market for the first 25 markets.
- Tier 1: $5 per qualifying market for the next 50 markets.
- Pool cap: $500 USDC. Hold window: 90 days from the qualifying event.
- Qualifying market: a Prophet market whose `creator.id` matches the user's bound `prophet_viewer_id` and whose `resolutionDate` is strictly before `2026-05-11T00:00:00Z`.

Earnings are conditional on the operator's daily reconciliation pass — the skill does not credit itself. The reconciler reads the markets this skill persists, re-fetches each one from Prophet GraphQL, and credits earnings via `seren-bounty`. **Earnings appear in `GET /users/me/earnings` within 24 hours of a qualifying market**, after the reconciliation pass runs.

## Required Inputs

- `prophet_email` — the email tied to the user's Prophet/Privy account; OTPs are delivered here.
- `email_provider` — `gmail` or `outlook`. Determines which publisher is used to read OTPs.
- `SEREN_API_KEY` — environment variable, or `API_KEY` injected by Seren Desktop. Required for every publisher call.

`bounty_id` is auto-resolved by the skill against open `customer_slug = prophet` bounties; the user does not pass it.

## First-Run Prophet Onboarding (zero-touch)

On first run the skill auto-creates the user's Prophet account from `prophet_email`: it walks Privy email-OTP, fills the onboarding form (username derived from the email's local-part with a hash-suffix fallback on collision, geo-attestation auto-ticked per Prophet's published ToS), and binds the user to the `AGENTACCESS` referral code so markets are attributed to the bounty operator's affiliate flow. No manual webapp visit required; the bind is permanent and re-runs are no-ops.

## Email + OTP Setup

The skill reads OTPs from the user's inbox via the `gmail` or `outlook` publisher. It only inspects the most recent unread message from `no-reply@mail.privy.io` (or the matching Prophet sender) and never moves, deletes, or marks other messages.

Configure the chosen publisher in **Seren Desktop → Settings → Publisher MCPs** and grant read scope. The skill will fail closed with a setup-style error if the publisher is not connected.

OTP delivery cadence:

- **Cold start:** the first run on a new machine or after a long idle triggers one OTP email.
- **Steady state:** once the in-process token-refresh worker is warm, it silently refreshes the Privy JWT every ~50 minutes using the refresh-token cookie — expect roughly **one OTP email per week** in normal operation.
- **Worst case:** if the refresh worker fails and every cron tick has to cold-start, the user will receive at most **4 OTP emails per day** (one per 6h tick).

If the OTP does not arrive within 90 seconds, the run records `status=blocked_otp` and the cron fires again on the next tick — the cron is **not** auto-paused for transient OTP delivery issues.

## Continuous Runs (seren-cron)

Default schedule is `0 */6 * * *` (every 6h, on the hour, UTC). The schedule lives in `seren-cron`; a long-lived local poller on the user's machine claims due ticks and runs `agent.py --command run` locally.

Three commands:

```bash
# 1. Register the runner and the local-pull job. Run once after setup.
python3 scripts/setup_cron.py create \
  --prophet-email "$PROPHET_EMAIL" \
  --email-provider gmail \
  --config config.json

# 2. Start the local poller. Leave this process running on the machine
#    that should execute the bounty work (e.g. via launchd, pm2, or
#    just leaving Seren Desktop open).
python3 scripts/run_local_pull_runner.py --config config.json

# 3. Pause / resume / delete the schedule.
python3 scripts/setup_cron.py list
python3 scripts/setup_cron.py pause  --job-id <job_id>
python3 scripts/setup_cron.py resume --job-id <job_id>
python3 scripts/setup_cron.py delete --job-id <job_id>
```

**Auto-pause behavior** (does not require user action; surfaced in seren-cron job state):

- **Bounty pool exhausted:** if a tick reports `reason=blocked_no_bounty`, the runner pauses the cron job before submitting the result. Resume manually after the operator funds a follow-on bounty.
- **Low SerenBucks (publisher 402):** if a tick fails because the user's prepaid balance can no longer cover Prophet GraphQL or `seren-models` calls, the runner pauses the cron job. Top up at `https://serendb.com/serenbucks`, then resume.

Transient failures (Prophet GraphQL down, OTP not delivered) do **not** auto-pause; the cron keeps firing and the runs table records the consecutive blocks for later inspection.

## Authentication and Privacy

- The Privy JWT is held in memory for the duration of a run and is **not persisted to disk after the run exits**. The token-refresh worker keeps a short-lived in-process cache; that cache is wiped when the runner process stops.
- OTP emails are read in-place via the gmail/outlook publisher. The OTP code is parsed from the message body and immediately consumed by the Playwright login step; it is never copied off-device beyond the publisher request.
- `SEREN_API_KEY` is read from the environment and used only to authenticate publisher calls. It is never persisted.
- The skill writes to a skill-owned SerenDB project (`prophet`) and database (`prophet`) for run history, created markets, and participant identity. No third party can read this storage; the operator's reconciler reads it via owner-scoped queries to attribute earnings.

## Minimal Run

```bash
cd prophet/prophet-bounty-runner
python3 -m pip install -r requirements.txt
cp config.example.json config.json
export SEREN_API_KEY=...

# One-time setup: verify auth, resolve bounty, bootstrap the SerenDB schema.
python3 scripts/agent.py --config config.json \
  --command setup \
  --prophet-email you@example.com

# Schedule and start the autonomous 6h runner.
python3 scripts/setup_cron.py create \
  --config config.json \
  --prophet-email you@example.com \
  --email-provider gmail
python3 scripts/run_local_pull_runner.py --config config.json
```

Manual one-off run (rare; the cron is the normal path):

```bash
python3 scripts/agent.py --config config.json \
  --command run \
  --prophet-email you@example.com \
  --email-provider gmail \
  --json-output
```

## Testnet Mode

Testnet support tracks the pattern documented by `prophet-market-seeder/SKILL.md` (Privy testnet base URL + USDC faucet at `0xa0f2da5e260486895d73086dd98af09c25dc2883c6ac96025a688f855c180d06`). The bounty-runner agent does **not** currently honor a `testnet` config block or the `PROPHET_TESTNET_MODE` env var. Real testnet integration may land in a follow-on revision; for now, validate against the production bounty (the user is the operator and the only implementer, so their first market accruing earnings against their own escrow is acceptable and was acknowledged at launch).

## Disclaimers

- Prophet is **mainnet** software. Markets created by this skill are real markets on Prophet's production deployment, settled in real USDC. The skill submits real `createMarket` mutations under the user's Privy account; bad submissions are visible to other Prophet users.
- Bounty earnings are subject to a **90-day hold** during which the operator can claw back fraudulent or invalid markets. A market that the operator clawbacks does not pay.
- Earnings are credited by the operator's reconciler, not by this skill. If the reconciler is paused or the operator's `customer_slug = prophet` privileges change, earnings may be delayed or rejected even after a market is created.
- This skill creates real Prophet markets. Submit only markets the user is willing to stand behind; the user's `prophet_viewer_id` is bound to every market created during a run.
- Trading prediction markets is regulated differently across jurisdictions. The user is responsible for ensuring participation is legal where they live.

## Troubleshooting

**OTP not delivered within 90 seconds.**
- Check the spam folder; the Privy sender is `no-reply@mail.privy.io`.
- Verify the gmail/outlook publisher is connected and has read scope in Seren Desktop → Settings → Publisher MCPs.
- The run records `status=blocked_otp`; the cron will fire again in 6h. To force an immediate retry, run `agent.py --command run` once by hand.

**Cron paused with no recent ticks.**
- Run `python3 scripts/setup_cron.py list` to find the pause reason. Two common causes:
  - `auto_pause_reason=pool_exhausted`: the bounty hit `max_pool_atomic`. Wait for the operator to publish a follow-on bounty, then `setup_cron.py resume`.
  - `auto_pause_reason=low_serenbucks`: top up at `https://serendb.com/serenbucks`, then `setup_cron.py resume`.
- If neither, check `python3 scripts/run_local_pull_runner.py` is still running on the user's machine.

**Dedup pass blocks every candidate.**
- If Prophet GraphQL is unreachable, the dedup-against-existing-markets pass refuses to submit (fail-closed). Re-run after Prophet recovers.
- If candidates are being filtered out as duplicates, confirm the `markets_created` ledger reflects the actual Prophet inventory by running `agent.py --command status`. Stale local state can be reconciled by deleting the local row and re-running; the operator's reconciler is idempotent on `prophet_market_id`.
