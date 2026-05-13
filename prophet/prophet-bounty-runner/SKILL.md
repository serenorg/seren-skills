---
name: prophet-bounty-runner
description: "Run the Prophet Polymarket-Mirror Sprint bounty workflow — agent-driven Privy OTP login via Seren Desktop's Playwright MCP, generate and submit Prophet markets, post proof to seren-bounty, and report earnings status."
---

# Prophet Bounty Runner

## For Claude: How to Use This Skill

This skill is split between two execution surfaces:

- **The agent (you, in Seren Desktop)** drives the browser via
  `mcp__playwright__*` tools and the inbox via the `gmail` /
  `outlook` publisher, captures the Privy JWT from
  `localStorage["privy:token"]`, and exports it as
  `PROPHET_SESSION_TOKEN`.
- **The Python subprocess** (`scripts/agent.py`) consumes that JWT
  and does the bounty / Polymarket / Prophet GraphQL work. It does
  **not** drive a browser and does **not** import `playwright`.

When invoked:

1. Drive the agent-side OTP flow (see "Agent-driven OTP runbook"
   below) and capture the JWT into `PROPHET_SESSION_TOKEN`.
2. Run `python3 scripts/agent.py --command setup` to verify auth
   and resolve an open bounty.
3. Run `PROPHET_SESSION_TOKEN=$JWT python3 scripts/agent.py
   --command run --json-output` once and confirm the result is
   `status=ok`. If it returns `status=blocked` with
   `reason=missing_session_token`, repeat step 1 — the JWT was
   not exported. If it returns `reason=blocked_otp` (viewer-bind
   failure) or `reason=blocked_no_bounty`, surface the blocker to
   the user and **do not** schedule cron until they acknowledge it.
   If every candidate produced `prophet.create_market_failed`
   events, refuse to schedule and report the failures.
4. Only after a successful (or explicitly acknowledged) first run,
   call `python3 scripts/setup_cron.py create` to enable
   autonomous 6h runs and start `python3
   scripts/run_local_pull_runner.py` to claim due ticks.

This validation gate prevents the cron and 30s local-pull poller
from accruing cost before the runner has produced any qualifying
market. After validation, the user does not normally invoke
`--command run` again manually — the cron drives it. Skill
instructions are preloaded in context when this skill is active;
do not perform filesystem searches or tool-driven exploration to
rediscover them.

## When to Use

- earn the prophet sprint bounty
- run the prophet bounty runner
- check my prophet bounty status
- mirror settling polymarket markets to prophet for the bounty

## What This Skill Does

- Logs into Prophet via the email-OTP flow, **driven by the agent
  using Seren Desktop's `mcp__playwright__*` tools**, reading the
  OTP in-place from the user's gmail or outlook inbox via the
  publisher.
- Discovers settling Polymarket markets that resolve before
  `2026-05-26`, scores and filters candidates, and submits them
  as new Prophet markets.
- Persists every created market to the skill-owned SerenDB with
  the user's `prophet_viewer_id` so the operator's daily
  reconciler can attribute earnings.
- Posts a cumulative proof submission to `seren-bounty` after
  each run.
- Runs autonomously every 6 hours via `seren-cron` once
  `setup_cron.py create` is called.
- Auto-pauses the cron job when the bounty pool is exhausted or
  SerenBucks runs low (publisher 402).

## What You Get Paid

Bounty terms (`customer_slug = prophet`):

- Tier 0: $10 per qualifying market for the first 25 markets.
- Tier 1: $5 per qualifying market for the next 50 markets.
- Pool cap: $500 USDC. Hold window: 90 days from the qualifying
  event.
- Qualifying market: a Prophet market whose `creator.id` matches
  the user's bound `prophet_viewer_id` and whose `resolutionDate`
  is strictly before `2026-05-26T00:00:00Z`.

Earnings are conditional on the operator's daily reconciliation
pass — the skill does not credit itself. The reconciler reads
the markets this skill persists, re-fetches each one from Prophet
GraphQL, and credits earnings via `seren-bounty`. **Earnings
appear in `GET /users/me/earnings` within 24 hours of a
qualifying market**, after the reconciliation pass runs.

## Required Inputs

- `prophet_email` — the email tied to the user's Prophet/Privy
  account; OTPs are delivered here.
- `email_provider` — `gmail` or `outlook`. Determines which
  publisher is used to read OTPs.
- `SEREN_API_KEY` — environment variable, or `API_KEY` injected
  by Seren Desktop. Required for every publisher call.
- `PROPHET_SESSION_TOKEN` — the Privy JWT the agent captured in
  step 1 below. **Required** by `scripts/agent.py`; missing →
  `status=blocked, reason=missing_session_token`.

`bounty_id` is auto-resolved by the skill against open
`customer_slug = prophet` bounties; the user does not pass it.

## Agent-driven OTP runbook

The agent layer drives Privy via Playwright MCP. This is the
"step 1" referenced in the For-Claude block above. Update the
named selector constants below if Prophet rotates them; the
sequence stays the same.

### Selector + sender constants

```text
PROPHET_APP_URL          = "https://app.prophetmarket.ai"
PRIVY_OTP_SENDER         = "no-reply@mail.privy.io"
SIGN_IN_BUTTON_SELECTOR  = 'button:has-text("Sign in")'
EMAIL_INPUT_SELECTOR     = '#email-input'
SUBMIT_BUTTON_SELECTOR   = 'button:has-text("Submit")'
OTP_INPUT_SELECTOR_FMT   = 'input[name="code-{i}"]'   # i in 0..5
GOT_IT_BUTTON_SELECTOR   = 'button:has-text("Got it!")'
REFERRAL_INPUT_SELECTOR  = 'input[name="referral-code"]'
REFERRAL_SUBMIT_SELECTOR = 'button:has-text("Submit")'
DEPOSIT_SKIP_SELECTOR    = 'button:has-text("Skip")'
LOCAL_STORAGE_TOKEN_KEY  = "privy:token"
AGENTACCESS_REFERRAL     = "AGENTACCESS"
```

Selectors rotate. Recent history: the sign-in button has been
`Connect` → `SIGN IN` → `Sign in`. When the agent observes a
mismatch, edit the constant block above as a one-line change.
The agent may also adapt on the fly when selectors drift.

### Steps

1. **Verify capabilities.** Confirm `mcp__playwright__*` tools
   are available. Confirm the `gmail` / `outlook` publisher
   matches `email_provider` and is reachable. If either check
   fails, surface a remediation message to the user and stop.

2. **Open Privy.**
   `playwright_navigate(url=PROPHET_APP_URL)`. If the page
   already shows a logged-in viewer (avatar, no sign-in button),
   skip to step 6 — the existing JWT is good.

3. **Submit email.** Click `SIGN_IN_BUTTON_SELECTOR`, fill
   `EMAIL_INPUT_SELECTOR` with `prophet_email`, click
   `SUBMIT_BUTTON_SELECTOR`. Privy sends a 6-digit OTP.

4. **Read the OTP from the inbox.** Poll the configured email
   publisher with
   `q=from:{PRIVY_OTP_SENDER} newer_than:5m` until a new message
   lands (timeout: 90s). Parse the 6-digit code from the snippet
   or body. Do not move, delete, or mark other messages.

5. **Submit the OTP.** Fill
   `input[name="code-0"]` through `input[name="code-5"]` with the
   six digits.

6. **Drive the new modal stack.** Prophet's onboarding now
   stacks three optional dialogs after the JWT lands:

   1. `Got it!` preview notice — click
      `GOT_IT_BUTTON_SELECTOR` if visible.
   2. Referral-code prompt — fill `REFERRAL_INPUT_SELECTOR` with
      `AGENTACCESS_REFERRAL` and click
      `REFERRAL_SUBMIT_SELECTOR`. This binds the user to the
      operator's affiliate flow so created markets attribute
      correctly. The bind is one-time and idempotent on re-run
      (Prophet swallows "already redeemed").
   3. `Deposit USDC` prompt — click `DEPOSIT_SKIP_SELECTOR`.

   Returning users skip the modal stack entirely — no-op when
   the dialogs are not present. The old `/onboarding` form
   (`#username` + `#geo-attestation`) has been removed by Prophet
   and the skill no longer drives it.

7. **Capture the JWT.** Read
   `localStorage["privy:token"]` via
   `playwright_evaluate(script='localStorage.getItem("privy:token")')`.
   The result may be JSON-quoted (`"eyJ..."`); strip the
   surrounding quotes if present. The JWT must start with `eyJ`
   and contain two `.` separators.

8. **Verify the JWT.** POST directly to
   `https://app.prophetmarket.ai/api/graphql` with
   `Authorization: Bearer <jwt>` and body
   `{"query":"query { viewer { user { id email } } }"}`. The
   response must contain a non-null `viewer.user.id`. If null,
   return to step 6 (the modal stack may not have completed) or
   step 3 (the JWT may be wrong account). Issue #493: the previous
   `prophet-ai` Seren publisher hop was removed because the
   gateway reserves `Authorization` for SEREN_API_KEY billing
   auth and Prophet ignored the `Cookie: privy-token=*`
   workaround entirely.

9. **Export and shell out.**
   `PROPHET_SESSION_TOKEN=<jwt> python3 scripts/agent.py
   --command run --json-output`. Surface `status` to the
   operator. If `status=ok`, optionally schedule cron.

The Privy JWT lifetime is ~1 hour. Re-run this runbook before
each bounty cycle (the cron operator does this on every tick).

## Agent-driven UI submission runbook

Phase 15 (#505): `createMarketWithBet` requires an in-browser Privy
signing prompt that no headless API can drive. After
`scripts/agent.py --command run` returns its envelope, every entry
under `pending_ui_submission` becomes one Playwright sequence the
agent (Claude in Seren Desktop) executes against
`https://app.prophetmarket.ai/create`.

### Selector + constants

```text
PROPHET_CREATE_URL              = "https://app.prophetmarket.ai/create"
ASK_PROPHET_TEXTAREA            = 'textarea[data-testid="market-prompt-input"]'
VALIDATE_QUESTION_BUTTON        = 'button[data-testid="validate-question-button"]'
CREATE_MARKET_BUTTON            = 'button[data-testid="create-market-button"]'
VALIDATION_ERROR_CONTAINER      = '[data-testid="validation-error"]'
GOT_IT_BUTTON                   = 'button:has-text("Got it!")'
ODDS_CALC_PROGRESS_SELECTOR     = 'text=/[0-9]+ of [0-9]+ models completed/'
```

Bet-form and confirm-button selectors are captured at the time the
agent observes them (Prophet rotates these periodically; the agent
adapts via `playwright_evaluate` against the live DOM).

### Sequence (per `pending_ui_submission` entry)

0. **Re-check execution headroom (issue #522).** Between the Python
   runner emitting `pending_ui_submission` and this Playwright
   sequence firing, seconds pass. Before driving any UI, compute
   `headroom = entry.resolution_date_iso - now()` and silently
   skip-and-advance to the next entry if `headroom <
   minimum_ui_headroom_seconds` (default 600s; the same value Python
   used at discovery time). Do **not** prompt the operator — this is
   automation, not a question. Record one
   `prophet.market_headroom_skipped` event so the run summary
   reflects the drop, then continue. The Phase-15 UI flow needs
   ~90–180s minimum (Validate ~10s + odds calc 60–120s + bet form +
   Privy prompt); a candidate inside the floor will lose the seed
   bet for nothing.
1. Reuse the cached Privy JWT (`localStorage["privy:token"]`); if
   absent, run the **Agent-driven OTP runbook** first.
2. `playwright_navigate(PROPHET_CREATE_URL)` and click
   `GOT_IT_BUTTON` if the preview overlay is present.
3. Fill `ASK_PROPHET_TEXTAREA` with `entry.question`.
4. Click `VALIDATE_QUESTION_BUTTON`. Poll for either the
   normalized title (success) or `VALIDATION_ERROR_CONTAINER` —
   the most common error is a resolution date outside the
   `[2026-05-11, 2026-05-24]` window. If validation fails, record
   `prophet.market_validate_failed` against `entry` and move on.
5. Click `CREATE_MARKET_BUTTON`. Poll
   `ODDS_CALC_PROGRESS_SELECTOR` until `6 of 6 models completed`
   (typical 60–120s; the 6th model is occasionally slow).
6. The bet form appears. Fill the YES/NO outcome and the
   `entry.initial_bet_usdc` amount, click the confirm button,
   accept the in-browser Privy signing prompt. Prophet debits
   `initial_bet_usdc` from the operator's Safe at this step
   (verified by Phase 15 live audit).
7. Capture the new `prophet_market_id` from the redirected URL
   (`/market/<id>` or `/market/<slug>`).
8. Persist by running:
   ```bash
   PROPHET_SESSION_TOKEN=$JWT python3 scripts/agent.py \
     --command record-created-market \
     --polymarket-market-id "$POLYMARKET_ID" \
     --prophet-market-id "$PROPHET_ID"
   ```
   *(Note: `record-created-market` is a follow-on revision; until
   it lands, the agent records the result by writing directly into
   the skill-owned SerenDB `markets_created` table.)*

If the bet form fails to render after a 180s odds-calc timeout,
abandon the candidate and record
`prophet.market_odds_calc_timeout`. The cron's next tick will not
retry this candidate automatically — the operator decides whether
to re-queue it manually.

## Email + OTP Setup

The skill reads OTPs from the user's inbox via the `gmail` or
`outlook` publisher. It only inspects the most recent unread
message from `no-reply@mail.privy.io` (or the matching Prophet
sender) and never moves, deletes, or marks other messages.

Configure the chosen publisher in **Seren Desktop → Settings →
Publisher MCPs** and grant read scope. The agent will fail
closed with a setup-style error if the publisher is not
connected.

OTP delivery cadence:

- **Per cycle:** Privy sends one OTP each time the agent
  re-runs the runbook. The cron tick interval is 6 hours, so
  expect one OTP email per 6h cycle.

If the OTP does not arrive within 90 seconds, the run records
`status=blocked_otp` and the cron fires again on the next tick
— the cron is **not** auto-paused for transient OTP delivery
issues.

## Continuous Runs (seren-cron)

Default schedule is `0 */6 * * *` (every 6h, on the hour, UTC).
The schedule lives in `seren-cron`; a long-lived local poller
on the user's machine claims due ticks, drives the
agent-side OTP runbook, and runs `agent.py --command run`
locally with `PROPHET_SESSION_TOKEN` set.

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

**Auto-pause behavior** (does not require user action; surfaced in
seren-cron job state):

- **Bounty pool exhausted:** if a tick reports
  `reason=blocked_no_bounty`, the runner pauses the cron job
  before submitting the result. Resume manually after the
  operator funds a follow-on bounty.
- **Low SerenBucks (publisher 402):** if a tick fails because
  the user's prepaid balance can no longer cover Prophet
  GraphQL or `seren-models` calls, the runner pauses the cron
  job. Top up at `https://serendb.com/serenbucks`, then resume.

Transient failures (Prophet GraphQL down, OTP not delivered, JWT
not captured) do **not** auto-pause; the cron keeps firing and
the runs table records the consecutive blocks for later
inspection.

## Authentication and Privacy

- The Privy JWT is held in memory for the duration of a run and
  is **not persisted to disk after the run exits**. It is
  exported into `PROPHET_SESSION_TOKEN` for the lifetime of the
  Python subprocess and discarded when that process exits.
- OTP emails are read in-place via the gmail/outlook publisher.
  The OTP code is parsed from the message body and immediately
  consumed by the agent's Playwright step; it is never copied
  off-device beyond the publisher request.
- `SEREN_API_KEY` is read from the environment and used only to
  authenticate publisher calls. It is never persisted.
- The skill writes to a skill-owned SerenDB project (`prophet`)
  and database (`prophet`) for run history, created markets, and
  participant identity. No third party can read this storage;
  the operator's reconciler reads it via owner-scoped queries
  to attribute earnings.

## Minimal Run

```bash
cd prophet/prophet-bounty-runner
python3 -m pip install -r requirements.txt
cp config.example.json config.json
export SEREN_API_KEY=...

# 1. One-time setup: verify auth, resolve bounty, bootstrap the SerenDB schema.
python3 scripts/agent.py --config config.json \
  --command setup \
  --prophet-email you@example.com

# 2. Drive the agent-side OTP runbook (above) to capture the JWT
#    into PROPHET_SESSION_TOKEN, then validate the runner end-to-end
#    before scheduling cron. Confirm the JSON output reports
#    `"status": "ok"`. If it reports `missing_session_token`,
#    `blocked_otp`, or `blocked_no_bounty`, resolve the blocker
#    and re-run this step before continuing.
PROPHET_SESSION_TOKEN="$JWT" python3 scripts/agent.py --config config.json \
  --command run \
  --prophet-email you@example.com \
  --email-provider gmail \
  --json-output

# 3. Schedule and start the autonomous 6h runner. Only run this
#    after the validation step above returned status=ok.
python3 scripts/setup_cron.py create \
  --config config.json \
  --prophet-email you@example.com \
  --email-provider gmail
python3 scripts/run_local_pull_runner.py --config config.json
```

## Disclaimers

- Prophet is **mainnet** software. Markets created by this skill
  are real markets on Prophet's production deployment, settled
  in real USDC. Bad submissions are visible to other Prophet users.
- **Phase 15 architecture (#505).** Live audit (2026-05-13) showed
  `createMarketWithBet` requires a **client-signed**
  `SignedOrderInput` that only the in-browser Privy SDK can produce —
  there is no agent-accessible API for signing on the user's behalf.
  The Python subprocess therefore stops at
  `marketCreationOrderParams` and emits the candidate set under
  `pending_ui_submission` in the run envelope. The agent (running
  in Seren Desktop with `mcp__playwright__*` tools) drives the
  Prophet `/create` UI to finalize each market: question →
  `Validate Question` (initiateMarket) → `Create Market`
  (startOddsCalculation, ~60–120s) → bet form → confirm (Privy
  signing prompt). See the **Agent-driven UI submission runbook**
  section below for the full selector sequence.
- Prophet's `/create` UI enforces a resolution-date window of
  `[2026-05-11, 2026-05-24]`. The skill's `BOUNTY_DEADLINE_ISO` is
  pinned to `2026-05-24` so candidates that would be rejected at
  `Validate Question` are dropped in `discover_polymarket_sources`
  before they reach the agent.
- Discovery now samples up to **500 settling Polymarket markets**
  per run (`_DEFAULT_LIMIT` in `scripts/polymarket/discovery.py`)
  so the tight window has enough qualifiers to choose from.
  `submit_limit` still caps how many of those candidates flow into
  `pending_ui_submission` per run.
- Bounty earnings are subject to a **90-day hold** during which
  the operator can claw back fraudulent or invalid markets. A
  market that the operator clawbacks does not pay.
- Earnings are credited by the operator's reconciler, not by
  this skill. If the reconciler is paused or the operator's
  `customer_slug = prophet` privileges change, earnings may be
  delayed or rejected even after a market is created.
- This skill creates real Prophet markets. Submit only markets
  the user is willing to stand behind; the user's
  `prophet_viewer_id` is bound to every market created during a
  run.
- Trading prediction markets is regulated differently across
  jurisdictions. The user is responsible for ensuring
  participation is legal where they live.

## Troubleshooting

**`status=blocked, reason=missing_session_token`.**

- The agent did not export `PROPHET_SESSION_TOKEN` before
  shelling out to `scripts/agent.py`. Re-run the agent-side
  OTP runbook (above) and ensure the JWT lands in
  `localStorage["privy:token"]`, then export it.

**OTP not delivered within 90 seconds.**

- Check the spam folder; the Privy sender is
  `no-reply@mail.privy.io`.
- Verify the gmail/outlook publisher is connected and has read
  scope in Seren Desktop → Settings → Publisher MCPs.
- The run records `status=blocked_otp`; the cron will fire
  again in 6h. To force an immediate retry, drive the runbook
  by hand.

**JWT captured but `status=blocked, reason=blocked_otp`.**

- The viewer-bind step (`viewer { user { id email } }` against
  `https://app.prophetmarket.ai/api/graphql`) rejected the JWT.
  Most common cause: the modal stack did not complete and the
  user has no Prophet user record yet. Re-run step 6 of the
  runbook (Got it! → referral code → skip deposit), then
  re-export `PROPHET_SESSION_TOKEN`. New-user creation only
  works through the browser-driven modal stack because
  Prophet's `registerWithPrivy` mutation requires the full
  Privy cookie jar that only the browser holds.

**Cron paused with no recent ticks.**

- Run `python3 scripts/setup_cron.py list` to find the pause
  reason. Two common causes:
  - `auto_pause_reason=pool_exhausted`: the bounty hit
    `max_pool_atomic`. Wait for the operator to publish a
    follow-on bounty, then `setup_cron.py resume`.
  - `auto_pause_reason=low_serenbucks`: top up at
    `https://serendb.com/serenbucks`, then `setup_cron.py
    resume`.

- If neither, check `python3 scripts/run_local_pull_runner.py`
  is still running on the user's machine.

**Dedup pass blocks every candidate.**

- If Prophet GraphQL is unreachable, the dedup-against-existing-
  markets pass refuses to submit (fail-closed). Re-run after
  Prophet recovers.
- If candidates are being filtered out as duplicates, confirm
  the `markets_created` ledger reflects the actual Prophet
  inventory by running `agent.py --command status`. Stale local
  state can be reconciled by deleting the local row and
  re-running; the operator's reconciler is idempotent on
  `prophet_market_id`.

**Selectors stopped matching.**

- Prophet rotates the sign-in button periodically (`Connect` →
  `SIGN IN` → `Sign in`). Update the selector constants in the
  "Selector + sender constants" block above. The agent should
  surface the failing selector in the run report so the edit
  is one line.
