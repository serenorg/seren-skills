---
name: prophet-arb-bot
description: Operator-arb between Prophet and Polymarket on operator-supplied market pairs.
author: Taariq Lewis
version: 0.1.0
---

# Prophet Arb Bot

## For Claude: How to Use This Skill

When invoked:

1. Run `python3 scripts/agent.py --command setup --json-output` to verify auth and apply the schema. Idempotent — safe to re-run.
2. Run `python3 scripts/agent.py --command run --yes-live --json-output` once. The result will be `status=ok` (cycle ran), `status=ok_no_fills` (cycle ran but no Prophet fills), or `status=blocked`.
3. **If the run envelope carries `pending_ui_submission`** (auto-discover mode found Polymarket candidates that don't have Prophet mirrors yet), drive the **Agent-driven UI submission runbook** below for each entry. The runbook drives `/create` through `startOddsCalculation`, calls `compute-seed-intent` to read Prophet's AI fair value out of the completed odds session and pick the seed/hedge sides, submits the Polymarket hedge first via `record-created-market`, and only clicks Prophet Confirm after the hedge succeeds. After driving the UI for every entry, re-run `agent.py --command run --yes-live` so the bot trades the newly-created pairs.
4. If `status=blocked`, surface the `reason` to the user and **do not** schedule cron until acknowledged.
5. Only after a successful first run, call `python3 scripts/setup_cron.py create --yes-live` and start `python3 scripts/run_local_pull_runner.py` to claim due ticks. `--yes-live` remains required for autonomous schedules as defense-in-depth.

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

## Execution Modes

Two execution modes are supported, selected by `execution_mode` in `config.json`:

- `single_leg` (explicit legacy Mode A) — the arb-bot trades exclusively on Prophet; the polymarket leg is a fair-value reference, not a hedge. Existing operators with `execution_mode="single_leg"` in their config are unaffected.
- `delta_neutral` (first-run default) — after every Prophet fill the bot submits the offsetting Polymarket order via `py-clob-client`. Both Prophet and Polymarket are on Polygon, so no cross-chain bridging is required; each leg locks its own USDC pool. A **pre-trade depth check** rejects opportunities where the Polymarket book can't absorb the planned notional at `max_hedge_slippage_bps` (default 200) — preventing naked Prophet exposure at the source. If the hedge submission fails post-fill, the bot invokes the Prophet `cancelOrder` unwind path and records the order with `hedge_status=naked_exposure` for operator action.

First-run bootstrap writes `"execution_mode": "delta_neutral"`, `"live_mode": true`, and `auto_discover.enabled=true`. `delta_neutral` is also a real claim against `POLY_PRIVATE_KEY` / `POLY_API_KEY` / `POLY_PASSPHRASE` / `POLY_SECRET` — the live hedger fails closed with `reason=polymarket_creds_missing` if those creds are not loaded.

## Auto-Discover Mode (#538)

When `auto_discover.enabled = true` in `config.json`, every `--command run` cycle:

1. **Fetches live Polymarket candidates** matching the campaign filter — active markets with 24h volume ≥ `min_24h_volume_usd` (default `$10,000`) that resolve in `[now + min_headroom_hours, resolution_deadline_iso]` (defaults: 24h headroom, 2026-05-24 deadline). Caps at `max_candidates` (default 50). No `manual_pairs` curation required — Jill invokes the skill and the campaign candidate set refreshes automatically.

2. **Looks up matching Prophet markets** via `viewer.markets`. Matched pairs are UPSERTed into `arb_pairs` with `source_skill='auto_discover'` and arbed on the same cycle. Question matching is normalized substring (lowercase, punctuation stripped) — Prophet's `/create` AI preserves question text near-verbatim from the operator's spreadsheet, so the matcher is tight enough to avoid false positives.

3. **Emits `pending_ui_submission`** for candidates Prophet hasn't created yet. The envelope shape is identical to the bounty-runner's, so the **Agent-driven UI submission runbook** below works for both skills without branching:
   ```json
   {
     "polymarket_market_id": "0x...",
     "question": "New York Yankees vs. Baltimore Orioles",
     "category": "Sports",
     "category_slug": "sports",
     "resolution_date_iso": "2026-05-18T22:00:00Z",
     "initial_bet_usdc": 1.0,
     "bounty_id": "",
     "prophet_viewer_id": "vid_...",
     "source_skill": "prophet-arb-bot"
   }
   ```
   Per entry, the agent drives Prophet `/create` through the bet form, calls `record-created-market` to submit the Polymarket hedge before clicking Prophet Confirm, then captures the new `prophet_market_id` from the redirected URL and calls `record-created-market` again to UPSERT the pair into `arb_pairs`. On the next `--command run --yes-live` tick, the bot trades the new pair.

4. **Refreshes the candidate sheet** at `state/arb_candidates.xlsx` (falls back to `.csv` if `openpyxl` is absent). Each row carries the pair status — `paired_this_run`, `already_paired`, `pending_prophet_creation`, or `unknown` — so the operator can audit the run's discovery output at a glance.

When auto-discover is disabled in an existing custom config, the existing `manual_pairs` flow is unchanged.

## What The Arb-Bot Is Not

- Not a market-maker.
- Not a Prophet market creator on its own — `pending_ui_submission` rows still require the agent to drive Prophet's `/create` UI via the Playwright runbook. The Python subprocess never signs the `createMarketWithBet` mutation directly (live-validated 2026-05-13: it requires a client-signed `SignedOrderInput` from the in-browser Privy SDK).
- Not a position liquidator. Both modes are cancel-only on the maker side; held YES/NO inventory must be unwound by the operator through the Prophet UI.

## Required Inputs

- `inputs.prophet_email` — same Privy account as the bounty-runner. Reuses the bounty-runner's session cache by default so the OTP flow only fires when both skills' caches are simultaneously stale.
- `inputs.email_provider` — `gmail` or `outlook`. Used only on cold-start cache refresh.
- `inputs.manual_pairs` — explicit (prophet_market_id, polymarket_condition_id) pairs. **Optional when `auto_discover.enabled=true`** — auto-discover refreshes the candidate set from live Polymarket each cycle. Use `manual_pairs` for pairs outside the campaign filter or to force-pin a specific market.
- `SEREN_API_KEY` — environment or `API_KEY` injected by Seren Desktop.

## Authentication

The arb-bot reuses prophet-bounty-runner's `~/.config/seren/skills/prophet-bounty-runner/state/privy_session.json` cache. If the cache is fresh (default leeway 60s before JWT expiry) the agent uses the cached JWT directly with **zero OTP emails**. If the cache is stale, the agent silently refreshes via the in-process refresh worker. Only when both fail does the cold-start OTP flow fire.

In practice:

- **Both skills running**: 0 extra OTP emails. The bounty-runner's 6h refresh keeps the cache fresh; the arb-bot rides along.
- **Arb-bot alone**: ~1 OTP/week.
- **Worst case**: 24/day (one per hourly tick) if every refresh fails.

You can also pre-supply a JWT via `PROPHET_SESSION_TOKEN` env var. The agent skips the OTP flow entirely in that case.

## Live Mode Safety

First-run mode is live-enabled delta-neutral, but Prophet's in-browser
Privy prompt remains the per-market consent gate. Autonomous schedules
still require both:

- `live_mode: true` in `config.json`
- `--yes-live` on the CLI (or `yes_live=true` in the seren-cron payload)

Without both on a scheduled run, the cycle still scores opportunities
and emits decision rows, but it never calls `placeOrder`.

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
3. Verify `inputs.manual_pairs` has at least one entry **OR** `auto_discover.enabled=true` (run `--command setup` first if neither is satisfied).
4. Verify `https://app.prophetmarket.ai/api/graphql` is reachable (the prophet-ai publisher hop is gone — see #493).
5. Verify the live `polymarket-data` publisher is reachable.
6. **Funds preflight (#524/#545):** after scoring, query `viewer.cashBalance.availableCents` once. In `single_leg` mode, if it cannot cover `sum(opp.size_usdc for opp in actionable[:max_orders_per_run])`, return a `status=blocked, reason=funds_insufficient, action=deposit_required` envelope before any `placeOrder` mutation fires. Seed preflight and trim run whenever `pending_ui_submission` is non-empty, regardless of `live_mode` or `--yes-live`, so the UI queue is always bankroll-trimmed and depth-filtered.
7. **Delta-neutral pre-trade additions (#536):** when `execution_mode = "delta_neutral"`, the cycle additionally must:
   - Have `py-clob-client` installed and `POLY_PRIVATE_KEY`/`POLY_API_KEY`/`POLY_PASSPHRASE`/`POLY_SECRET` loaded.
   - Reach the Polymarket CLOB and fetch order-book depth for every actionable pair. Opportunities whose visible Polymarket depth can't cover the target notional at `max_hedge_slippage_bps` are rejected with `polymarket_depth_*` blockers before the Prophet limit is posted.
   - Run the **two-venue funds preflight**: query both Prophet protocol cash AND Polymarket CLOB collateral. The blocked envelope returns `prophet_deficit_usdc` and `polymarket_deficit_usdc` as separate fields so the agent's deposit runbook can route to the right venue.
8. If any check fails, fail closed with a structured `blocked` envelope and let the cron retry on the next tick.

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

## Delta-neutral hedge flow (#536)

When `execution_mode = "delta_neutral"`, every cycle follows this two-leg sequence:

1. **Post-fill sweep (top of cycle).** `list_user_orders` is called first.
   Any previously-open Prophet order now reporting `filled_shares > 0`
   triggers an immediate Polymarket hedge — opposite side, same notional,
   marketable price snapped to live tick. The hedge order id, fill
   quantity, and fill price are persisted to `arb_orders` alongside the
   Prophet leg under `hedge_status='hedged'`. Latency target: <5s between
   fill detection and Polymarket submission.

2. **Pre-trade depth check.** Each scored opportunity goes through
   `assess_polymarket_depth` before the Prophet limit is posted. If the
   visible Polymarket book can't cover `size_usdc` at acceptable slippage,
   the opportunity is rejected this cycle with a `polymarket_depth_*`
   blocker. This makes the "Prophet fills, Polymarket can't hedge" failure
   path impossible at the source — we don't quote Prophet exposure we
   can't immediately offset.

3. **Two-venue funds preflight.** Both Prophet protocol cash and
   Polymarket CLOB collateral are checked. The blocked envelope returns
   separate `prophet_deficit_usdc` and `polymarket_deficit_usdc` so the
   deposit runbook routes to the right venue.

4. **Hedge-failure path.** If the hedge submission throws after a Prophet
   fill (book moved between depth check and submission, CLOB rejection,
   balance shortfall on Polymarket), the bot invokes Prophet's
   `cancelOrder` for cleanup (no-op if already fully filled) and records
   `hedge_status='naked_exposure'`. Prophet has no force-close on the maker
   side, so naked exposure is honestly surfaced rather than silently
   accumulated — the operator must unwind the Prophet leg manually via
   the Prophet UI.

Schema: `arb_orders` carries four delta-neutral columns
(`polymarket_filled_qty`, `polymarket_fill_price`, `polymarket_order_id`,
`hedge_status`). They default to neutral values for `single_leg` rows so
existing operators see no migration churn.

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
export SEREN_API_KEY=...

# 1. Validate config and auth. Auto-bootstraps config.json from
#    config.example.json on first run with auto_discover.enabled=true
#    execution_mode=delta_neutral and live_mode=true. Email + provider
#    are persisted from flags; existing configs are never overwritten.
python3 scripts/agent.py --config config.json --command setup --json-output \
  --prophet-email you@example.com --email-provider gmail

# 2. Validate the runner end-to-end before scheduling cron. Confirm the
#    JSON output reports `"status": "ok"`. If it reports a `blocked`
#    status (e.g. `funds_insufficient_for_seeds`), resolve the blocker
#    using the deposit envelope and re-run before continuing.
python3 scripts/agent.py --config config.json --command run --yes-live --json-output

# 3. Schedule and start the autonomous hourly runner. Use --yes-live
#    only after the validation step above returned status=ok.
python3 scripts/setup_cron.py create \
  --config config.json \
  --prophet-email you@example.com \
  --email-provider gmail
python3 scripts/run_local_pull_runner.py --config config.json
```

## Seed bet preflight (#542)

When `auto_discover.enabled = true`, every `pending_ui_submission`
entry will cost the operator `initial_bet_usdc` on Prophet at confirm
time. In `delta_neutral` mode, it costs the same amount again on
Polymarket for the hedge (see below). Before emitting the list, the
runner now:

1. Queries Prophet `viewer.cashBalance` and (in delta-neutral mode)
   `DirectClobTrader.get_cash_balance` to compute
   `max_fundable = min(prophet_floor, polymarket_floor)`.
2. Filters candidates by Polymarket hedge depth (`assess_polymarket_depth`)
   when delta-neutral is active.
3. Ranks survivors by Polymarket 24h volume (proxy for spread potential
   since pre-creation pairs have no Prophet odds yet) and trims to
   `max_fundable` entries.

If `max_fundable == 0` the cycle returns
`status=blocked, reason=funds_insufficient_for_seeds` with a
`deposit_required_for_seeds` envelope carrying split deficits per venue.

## Agent-driven UI submission runbook

For each `pending_ui_submission` entry:

1. Navigate to `https://app.prophetmarket.ai/create`, fill the question,
   click `Validate Question`, then click `Create Market`. Capture the
   `OddsCalculationSession.id` returned by `startOddsCalculation` — the
   agent reads it from the network response or the page state.

2. **Compute the seed-side decision from Prophet's AI fair value (#548 / #551).**
   The 6-model calc runs for 60–180s. Poll the session and derive the
   intent in one call. `--polymarket-yes-price` is optional — if omitted
   (or `0.0`) the runner fetches the Polymarket book and derives the
   YES price from the midpoint (#551), so the agent does not need a
   separate price-fetch step:

   ```bash
   PROPHET_SESSION_TOKEN="$JWT" python3 scripts/agent.py \
     --command compute-seed-intent \
     --odds-session-id "$OCS_ID" \
     --polymarket-condition-id "$POLY_CID" \
     --json-output
   ```

   Pass `--polymarket-yes-price <value>` only when you want to pin a
   specific value (e.g. a freshly-observed mark at `/create` time). The
   envelope reports `polymarket_yes_price_source` ∈
   {`caller_supplied`, `book_midpoint`, `book_best_bid_only`,
   `book_best_ask_only`} so the agent can log which value was used.

   - `status=ok, reason=seed_intent_ready` → use `seed_side`,
     `hedge_price`, and `tick_size` from the payload for the next steps.
     Render `edge_summary` (e.g. `Prophet 58.0% vs Polymarket 42.0%
     → 1600 bps edge (BUY YES on Prophet)`) in the per-market log.
   - `status=blocked, reason=odds_session_not_completed` → Prophet
     rejected or failed the calc. Abandon this entry. No exposure was
     created on either side.
   - `status=blocked, reason=prophet_market_not_viable` → Prophet
     completed but marked `isViable=false`. Abandon this entry.
   - `status=blocked, reason=no_edge` → Prophet's fair value matches the
     Polymarket price within the configured floor. Abandon — the seed
     bet would have negative expected value. `edge_summary` carries the
     side-by-side (e.g. `Prophet 50.0% vs Polymarket 50.0% → 0 bps
     no_edge`) so Jill sees the why in the run summary.
   - `status=blocked, reason=polymarket_book_unavailable` → the live
     polymarket-data publisher is down, or the book had no usable
     bid/ask to derive a midpoint. Retry on the next tick.
   - `status=blocked, reason=prophet_unauthorized` (#553) → the cached
     Privy JWT is stale. Run the **Agent-driven OTP runbook** to refresh
     it, re-export `PROPHET_SESSION_TOKEN`, and retry the same
     `compute-seed-intent` call. The market is still in the odds-session
     window — do not abandon the candidate.
   - `status=blocked, reason=odds_session_timeout` (#553) → Prophet's
     6-model AI calc did not complete within `--poll-timeout-s` (default
     180s). Abandon this entry. No exposure was created on either side.

3. Once the bet form renders, fill the seed side returned by
   `compute-seed-intent` (`buy` or `sell`) and `entry.initial_bet_usdc`,
   but do **not** click the Prophet Confirm / Privy signing prompt yet.

4. Submit the Polymarket hedge first using the prices the previous step
   returned:

   ```bash
   python3 scripts/agent.py --command record-created-market \
     --polymarket-condition-id "$POLY_CID" \
     --prophet-seed-side "$SEED_SIDE" \
     --polymarket-marketable-price "$HEDGE_PRICE" \
     --seed-size-usdc "$INITIAL_BET_USDC" \
     --json-output
   ```

5. If the response has `hedge_status='hedge_failed_no_commit'`, stop
   this entry and do not click Prophet Confirm. No Prophet exposure was
   created.
6. If the response has `hedge_status='hedged'` and
   `next_action='click_prophet_confirm'`, click the Prophet Confirm /
   Privy prompt.
7. If Prophet Confirm succeeds, capture the redirected
   `prophet_market_id` and persist the pair:

   ```bash
   python3 scripts/agent.py --command record-created-market \
     --polymarket-condition-id "$POLY_CID" \
     --prophet-market-id "$PROPHET_MID" \
     --json-output
   ```

8. If Prophet Confirm fails or the operator declines after the
   Polymarket hedge filled, immediately unwind the Polymarket leg using
   the opposite-side marketable price from the live book:

   ```bash
   python3 scripts/agent.py --command record-created-market \
     --polymarket-condition-id "$POLY_CID" \
     --prophet-seed-side "$SEED_SIDE" \
     --polymarket-marketable-price "$UNWIND_PRICE" \
     --seed-size-usdc "$INITIAL_BET_USDC" \
     --prophet-confirm-declined \
     --json-output
   ```

## Delta-neutral seed creation (#542)

Single-leg mode (`execution_mode = "single_leg"`) commits the Prophet
seed bet unhedged; held YES/NO inventory resolves with the market.

Delta-neutral mode (`execution_mode = "delta_neutral"`) hedges every
seed before Prophet Confirm:

1. The agent fills Prophet's `/create` UI through the bet form without
   clicking Confirm.
2. The agent invokes:

   ```bash
   python3 scripts/agent.py --command record-created-market \
     --polymarket-condition-id "$POLY_CID" \
     --prophet-seed-side buy \
     --polymarket-marketable-price 0.001
   ```

3. If the runner returns `hedge_status='hedged'`, the agent clicks
   Prophet Confirm and captures the resulting `prophet_market_id`.
4. The agent persists the pair with
   `record-created-market --polymarket-condition-id "$POLY_CID"
   --prophet-market-id "$PROPHET_MID"`.

Seed hedge statuses:

- `hedged` — Polymarket accepted the pre-confirm hedge; click Prophet
  Confirm next.
- `hedge_failed_no_commit` — Polymarket rejected the hedge before
  Prophet Confirm; abort this entry with no exposure.
- `unwound_after_prophet_decline` — Polymarket filled, Prophet Confirm
  failed or was declined, and the Polymarket leg was reversed.
- `naked_exposure` — an unwind attempt after Prophet decline failed;
  operator action is required on Polymarket.

## Disclaimers

- Prophet is **mainnet** software. Orders submitted by this skill trade real USDC against real counterparties on Prophet's production deployment. Bad orders are not reversible; review your `min_spread` / `kelly_fraction` / `max_trade_size_usdc` before approving any Prophet Privy prompt or enabling an autonomous live schedule.
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
