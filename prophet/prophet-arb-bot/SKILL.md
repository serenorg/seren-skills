---
name: prophet-arb-bot
description: Operator-arb between Prophet and Polymarket on operator-supplied market pairs.
author: Taariq Lewis
version: 0.1.0
---

# Prophet Arb Bot

## For Claude: How to Use This Skill

When invoked:

1. Run `python3 scripts/agent.py --command setup --json-output` to verify auth and validate the operator's `inputs.manual_pairs`. Idempotent — safe to re-run.
2. Run `python3 scripts/agent.py --command run --json-output` once and confirm the result is `status=ok` (or `status=ok_no_fills`). If `status=blocked`, surface the `reason` to the user and **do not** schedule cron until they acknowledge it.
3. Only after a successful first run, call `python3 scripts/setup_cron.py create --yes-live` and start `python3 scripts/run_local_pull_runner.py` to claim due ticks. Without `--yes-live` the cron emits dry-run intents only.

This validation gate prevents the cron and 12h local-pull poller from accruing cost before the runner has produced any qualifying scoring pass. Skill instructions are preloaded in context when this skill is active; do not perform filesystem searches or tool-driven exploration to rediscover them.

## When to Use

- arb prophet markets against polymarket consensus
- run the prophet arb bot
- check my prophet arb bot status
- harvest spread between prophet and polymarket on markets I created

## What This Skill Does

Mode A operator-arb. For each operator-supplied (prophet_market_id, polymarket_condition_id) pair the agent:

1. Fetches Prophet's current YES/NO odds directly from `https://app.prophetmarket.ai/api/graphql` (#493: the previous `prophet-ai` Seren publisher hop is gone — see `scripts/prophet/transport.py`).
2. Fetches Polymarket's current YES/NO mid-price via the `polymarket-data` publisher.
3. Scores the spread `prophet_yes − polymarket_yes`. If the absolute spread exceeds the configured `min_spread` (default 3 ¢) and stays under `max_spread` (default 30 ¢), the agent emits a quoted limit order on Prophet that fades the drift.
4. Optionally enriches each pair with `seren-polymarket-intelligence` correlation/volatility data; high polymarket volatility downgrades the opportunity to watchlist-only.
5. Skips pairs where Prophet already has an open order at the same outcome+side, so two consecutive ticks don't double-quote.

Every cycle persists the run shell, scored opportunities, and submitted orders to SerenDB (`prophet/prophet`). The `arb_runs`, `arb_opportunities`, and `arb_orders` tables are the canonical run history. The agent also emits a single JSON envelope on stdout for `seren-cron` to capture in its `execution_results` table.

## What Mode A Is Not

- Mode A is not delta-neutral. The arb-bot trades exclusively on Prophet; the polymarket leg is a fair-value reference, not a hedge. Adding the polymarket hedge requires CCTP/Polygon plumbing that doubles the surface and is deferred to a later mode.
- Mode A is not a market-maker.
- Mode A does not create new Prophet markets — that's the bounty-runner's job.

## Required Inputs

- `inputs.prophet_email` — same Privy account as the bounty-runner. Reuses the bounty-runner's session cache by default so the OTP flow only fires when both skills' caches are simultaneously stale.
- `inputs.email_provider` — `gmail` or `outlook`. Used only on cold-start cache refresh.
- `inputs.manual_pairs` — the list of pairs the agent trades. Each entry: `{ "prophet_market_id": "...", "polymarket_condition_id": "..." }`. Populate this from your bounty-runner's recent activity (the prophet markets it created carry the polymarket conditionId they were seeded from).
- `SEREN_API_KEY` — environment or `API_KEY` injected by Seren Desktop.

## Authentication

The arb-bot reuses prophet-bounty-runner's `~/.config/seren/skills/prophet-bounty-runner/state/privy_session.json` cache. If the cache is fresh (default leeway 60s before JWT expiry) the agent uses the cached JWT directly with **zero OTP emails**. If the cache is stale, the agent silently refreshes via the in-process refresh worker. Only when both fail does the cold-start OTP flow fire.

In practice:

- **Both skills running**: 0 extra OTP emails. The bounty-runner's 6h refresh keeps the cache fresh; the arb-bot rides along.
- **Arb-bot alone**: ~1 OTP/week.
- **Worst case**: 24/day (one per hourly tick) if every refresh fails.

You can also pre-supply a JWT via `PROPHET_SESSION_TOKEN` env var. The agent skips the OTP flow entirely in that case.

## Live Mode Safety

Default mode is **dry-run**. Live trading requires both:

- `live_mode: true` in `config.json`
- `--yes-live` on the CLI (or `yes_live=true` in the seren-cron payload)

Without both, the cycle still scores opportunities and emits the decision rows in the JSON output, but never calls `placeOrder`.

## Trade Execution Contract

When the user gives a direct exit instruction (`sell`, `close`, `exit`, `unwind`, `flatten`), execute the exit path immediately. Cancel every open prophet order surfaced by `--command status` and ask only the minimum clarifying question if the user also wants to liquidate held positions (which cannot be force-sold on Prophet without an offsetting order).

## CLOB Exit Rules

The arb-bot is a passive quoter — it submits LIMIT orders that rest on Prophet's CTF order book and waits for fills. It does not place marketable taker orders. The exit posture follows from that:

- **Cancel-only on emergency exit.** The arb-bot does not submit a marketable sell to flatten inventory. The emergency path walks `viewer.orders` and cancels every open order surfaced. Held YES/NO positions stay on book and must be unwound by the operator out-of-band — Prophet has no force-close for the maker side.
- **Quotes are snapped to Prophet's `tick_size`.** Prices are submitted via `PlaceOrderInput.priceBps` (Int, 0–10000 basis points). Prophet's implicit `tick size` is 1 bp ($0.0001). Quotes that violate the tick are rejected at submission.
- **Never quote a passive sell above the best bid for an immediate exit.** Because the arb-bot's only exit primitive is `cancel_all`, there is no "passive sell at best bid" path on the user's behalf. If the user wants to liquidate YES at the best bid, they use Prophet's UI directly; the arb-bot will not sweep visible bid depth across the full book to flatten.
- **Visible-book recovery is not estimated.** The arb-bot does not size taker exits; it never reads the full book to walk levels, so `estimated_fill_size` / `estimated_exit_value` numbers are not produced.

## Emergency Exit

The emergency-exit path is `cancel_all` over `viewer.orders` — no marketable sells, no position liquidation:

1. Stop emitting new orders for the cycle.
2. Walk every open order surfaced by `--command status` and cancel each via `cancelOrder`.
3. Report the cancellation count and any errors in the run envelope.
4. Leave held YES/NO positions alone — Prophet's CTF settles on resolution, not on demand.

If the user needs immediate exposure removal (close all / unwind / flatten), they must liquidate via the Prophet UI manually. The arb-bot intentionally refuses to submit marketable sells on the user's behalf.

## Pre-Trade Checklist

Before any live `run --yes-live`:

1. Verify `SEREN_API_KEY` is loaded.
2. Verify a fresh JWT is available — either via `PROPHET_SESSION_TOKEN` env or the bounty-runner's session cache.
3. Verify `inputs.manual_pairs` has at least one entry (run `--command setup` first if not).
4. Verify `https://app.prophetmarket.ai/api/graphql` is reachable (the prophet-ai publisher hop is gone — see #493).
5. Verify the live `polymarket-data` publisher is reachable.
6. **Funds preflight (#524):** after scoring, query `viewer.cashBalance.availableCents` once. If it cannot cover `sum(opp.size_usdc for opp in actionable[:max_orders_per_run])`, return a `status=blocked, reason=funds_insufficient, action=deposit_required` envelope before any `placeOrder` mutation fires. Skip preflight in dry-run cycles.
7. If any check fails, fail closed with a structured `blocked` envelope and let the cron retry on the next tick.

## Agent-driven deposit runbook

Issue #524: every `placeOrder` LIMIT submission locks USDC collateral
on Prophet's CTF order book. The Python runner runs a funds preflight
after scoring and before the placement loop fires. If protocol cash
can't cover the planned collateral, `cmd_run` returns:

```json
{
  "status": "blocked",
  "reason": "funds_insufficient",
  "payload": {
    "action": "deposit_required",
    "deposit": {
      "chain": "polygon",
      "chain_id": 137,
      "usdc_contract_polygon": "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",
      "available_usdc": 0.0,
      "needed_usdc": 12.0,
      "deficit_usdc": 12.0
    }
  }
}
```

When this envelope lands, the agent (Claude in Seren Desktop) drives
Prophet's deposit UI before the next tick.

### Two cash sources

Prophet protocol cash (`viewer.cashBalance.availableCents`) is funded
by USDC deposited into the operator's Safe on Polygon (chainId 137):

1. **On-chain USDC exists but not yet deposited.** Query the Polygon
   native USDC contract `0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359`
   for the operator's Safe via the `seren-polygon` publisher
   (`eth_call` → `balanceOf(address)`). If on-chain ≥ deficit, drive
   Prophet's `/wallet → Deposit` UI to move it into protocol cash.

2. **No on-chain USDC available.** Surface the Safe address and
   `deficit_usdc`. Stop and let the operator fund externally.

### Selector + constants

```text
PROPHET_WALLET_URL             = "https://app.prophetmarket.ai/wallet"
DEPOSIT_BUTTON_SELECTOR        = 'button:has-text("Deposit")'
DEPOSIT_AMOUNT_INPUT_SELECTOR  = 'input[data-testid="deposit-amount-input"]'
DEPOSIT_CONFIRM_BUTTON         = 'button:has-text("Confirm")'
```

### Sequence

1. Read `deposit.safe_wallet_address` (when surfaced) from the blocked envelope.
2. Query on-chain USDC at that address via `seren-polygon`.
3. If on-chain USDC < `deposit.deficit_usdc`, surface and stop.
4. Otherwise navigate to `PROPHET_WALLET_URL`, click Deposit, fill
   the amount = `deficit_usdc`, click Confirm, accept the Privy
   signing prompt.
5. Poll `viewer.cashBalance.availableCents` until the deposit lands.
6. Re-run `agent.py --command run --json-output`.

### Cron behavior

`funds_insufficient` is **not** auto-paused like the publisher 402
low-SerenBucks case. The cron keeps firing; each tick re-checks the
balance. This is correct when the operator funds externally between
ticks. The blocker `funds_insufficient_by_<deficit>_usdc` is recorded
on the run so the operator can see the gap without re-running.

## `placeOrder` is server-signed (#505 Phase 15)

The Prophet `placeOrder` mutation is **server-signed**. Unlike
`createMarketWithBet` (which requires a client-signed
`SignedOrderInput`), `PlaceOrderInput` carries no signature — Prophet's
backend signs the CTF order on behalf of the user via the user's Privy
embedded wallet. Just the Privy JWT in the `Authorization` header is
sufficient.

The mutation shape is live-validated against Prophet's production
GraphQL endpoint (2026-05-13) and pinned in
`tests/fixtures/prophet_schema.json`:

```graphql
mutation PlaceOrder($input: PlaceOrderInput!) {
  placeOrder(input: $input) {
    order { id status side outcome type priceBps quantityShares filledShares remainingShares }
    cashBalance { availableCents totalCents }
    errors { field message code }
  }
}
```

`PlaceOrderInput = {marketId, outcome, type, side, priceBps, quantity, timeInForce}`.

If Prophet drifts the schema later, capture the new shape with:

```bash
SEREN_API_KEY=... PROPHET_SESSION_TOKEN='eyJ...' \
  python3 scripts/agent.py --command probe-schema
```

## Continuous Runs (seren-cron)

Default schedule is `0 * * * *` (every hour, on the hour, UTC). The schedule lives in `seren-cron`; a long-lived local poller on the user's machine claims due ticks and runs `agent.py --command run` locally.

```bash
# 1. Register the runner and the local-pull job. Run once after setup.
python3 scripts/setup_cron.py create \
  --prophet-email "$PROPHET_EMAIL" \
  --email-provider gmail \
  --config config.json \
  --yes-live

# 2. Start the local poller. Leave this process running on the machine
#    that should execute the arb work (e.g. via launchd, pm2, or just
#    leaving Seren Desktop open).
python3 scripts/run_local_pull_runner.py --config config.json

# 3. Pause / resume / delete the schedule.
python3 scripts/setup_cron.py list
python3 scripts/setup_cron.py pause  --job-id <job_id>
python3 scripts/setup_cron.py resume --job-id <job_id>
python3 scripts/setup_cron.py delete --job-id <job_id>
```

**Auto-pause** triggers on a publisher 402 (low SerenBucks). Top up at `https://serendb.com/serenbucks` and `setup_cron.py resume`.

Transient failures (Prophet GraphQL down, polymarket-data 5xx, OTP not delivered) do **not** auto-pause; the cron keeps firing and seren-cron's execution_results table records the consecutive blocks for later inspection.

## Persistence

The skill writes to SerenDB project=`prophet`, database=`prophet` (shared with the bounty-runner). On `--command setup` the agent resolves the project/database via the `seren-db` publisher's `/projects` + `/databases` endpoints, fetches a Postgres connection URI from `/projects/{id}/connection_uri`, and applies `serendb_schema.sql` over a `psycopg2` connection. Every cycle then writes opportunities and orders to that database.

Tables (created on first `setup`):
- `arb_pairs` — prophet ↔ polymarket binding
- `arb_runs` — one row per `--command run`
- `arb_opportunities` — every scored opportunity (acted on or skipped)
- `arb_orders` — submitted orders + last-seen status
- `arb_positions` — open holdings (computed from fills, populated in a future revision)
- `arb_pnl_snapshots` — daily mark-to-market (populated in a future revision)

The seren-db publisher does not expose an HTTP `run-sql` endpoint; SQL execution happens via the connection URI it returns. `psycopg2-binary` is a runtime dependency (see `requirements.txt`).

Cross-skill read: `discover_pairs_from_bounty_runner` SELECTs from `markets_created` if the bounty-runner has migrated to SerenDB persistence. Until then it returns [] silently and the operator seeds pairs via `inputs.manual_pairs`.

## Minimal Run

```bash
cd prophet/prophet-arb-bot
python3 -m pip install -r requirements.txt
cp config.example.json config.json
# Edit config.json: set inputs.prophet_email, inputs.email_provider,
# and at least one entry in inputs.manual_pairs.
export SEREN_API_KEY=...

# 1. Validate config and auth.
python3 scripts/agent.py --config config.json --command setup --json-output

# 2. Validate the runner end-to-end before scheduling cron. Confirm the
#    JSON output reports `"status": "ok"`. If it reports a `blocked`
#    status, resolve the blocker and re-run before continuing.
python3 scripts/agent.py --config config.json --command run --json-output

# 3. Schedule and start the autonomous hourly runner. Add --yes-live to
#    the setup_cron call only after the validation step above returned
#    status=ok and the operator has reviewed at least one dry-run cycle.
python3 scripts/setup_cron.py create \
  --config config.json \
  --prophet-email you@example.com \
  --email-provider gmail
python3 scripts/run_local_pull_runner.py --config config.json
```

## Disclaimers

- Prophet is **mainnet** software. Orders submitted by this skill trade real USDC against real counterparties on Prophet's production deployment. Bad orders are not reversible; review your `min_spread` / `kelly_fraction` / `max_trade_size_usdc` before flipping `live_mode=true`.
- This skill does not provide financial advice. Trading prediction markets is regulated differently across jurisdictions; the user is responsible for ensuring participation is legal where they live.

## Troubleshooting

**`reason=no_pairs_configured`.**

- `inputs.manual_pairs` is empty in `config.json`. Add at least one `{prophet_market_id, polymarket_condition_id}` pair.

**`reason=blocked_otp_email_missing`.**

- `inputs.prophet_email` is empty in `config.json`. Set it to the same email the bounty-runner uses.

**`reason=prophet_unauthorized` on every tick.**

- The session cache is stale and the silent refresh path failed. Restart the bounty-runner once to refresh the cache, or run `agent.py --command run` interactively to trigger the OTP flow.

**`blockers` contains `place_order_failed:ProphetSchemaError`.**

- Prophet's `placeOrder` mutation drifted. Run `agent.py --command probe-schema` to capture the live introspection, diff against `tests/fixtures/prophet_schema.json`, and update `scripts/prophet/orders.py` to match the new shape.

**`blockers` contains `duplicate_open_order:...`.**

- The arb-bot already has an open order at this outcome+side. By design — the bot does not double-quote.
