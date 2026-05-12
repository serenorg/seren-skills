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

## CLOB / Order-Book Exit Rules

For any CLOB or order-book exit path used by this skill, immediate exits must be marketable, not passive. Cancel all resting orders first, fetch the latest full book, then price a sell exit at the current `tick_size` minimum so it crosses the best bid instead of resting on the book. Never use a passive sell or post-only order for an immediate `sell`, `close`, `exit`, `unwind`, or `flatten` instruction.

Before submitting the exit, compute a visible-depth recovery estimate by sweeping visible bid depth across all levels for the requested size. Surface `best bid`, `best ask`, `tick_size`, estimated fill size, estimated unfilled size, estimated average price, and estimated exit value in the run output so the operator can see how much inventory the live book can absorb.

## Pre-Trade Checklist

Before any live `run --yes-live`:

1. Verify `SEREN_API_KEY` is loaded.
2. Verify a fresh JWT is available — either via `PROPHET_SESSION_TOKEN` env or the bounty-runner's session cache.
3. Verify `inputs.manual_pairs` has at least one entry (run `--command setup` first if not).
4. Verify `https://app.prophetmarket.ai/api/graphql` is reachable (the prophet-ai publisher hop is gone — see #493).
5. Verify the live `polymarket-data` publisher is reachable.
6. If any check fails, fail closed with a structured `blocked` envelope and let the cron retry on the next tick.

## Best-Guess `placeOrder` Notice

The Prophet `placeOrder` mutation has not yet been live-introspected against the canonical schema; the input shape in `scripts/prophet/orders.py` is a best-guess derived from the bounty-runner's `createMarketWithBet` pattern. If the live schema rejects the call, `ProphetSchemaError` is raised, the order is marked `blocked`, and the agent records the GraphQL error in the JSON envelope's `blockers` array so the operator can update the mutation shape and re-deploy.

To capture the live schema and pin field names, run:

```bash
SEREN_API_KEY=... PROPHET_SESSION_TOKEN='eyJ...' \
  python3 scripts/agent.py --command probe-schema
```

This writes `tests/fixtures/prophet_schema.json`. A follow-on PR will replace the best-guess shape with whatever the fixture pins.

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
- The `placeOrder` GraphQL shape is a best guess until the schema fixture lands. If a live order is rejected, the agent fails closed; if a live order is accepted at a price the operator did not expect, the operator carries that risk.
- This skill does not provide financial advice. Trading prediction markets is regulated differently across jurisdictions; the user is responsible for ensuring participation is legal where they live.

## Troubleshooting

**`reason=no_pairs_configured`.**

- `inputs.manual_pairs` is empty in `config.json`. Add at least one `{prophet_market_id, polymarket_condition_id}` pair.

**`reason=blocked_otp_email_missing`.**

- `inputs.prophet_email` is empty in `config.json`. Set it to the same email the bounty-runner uses.

**`reason=prophet_unauthorized` on every tick.**

- The session cache is stale and the silent refresh path failed. Restart the bounty-runner once to refresh the cache, or run `agent.py --command run` interactively to trigger the OTP flow.

**`blockers` contains `place_order_failed:ProphetSchemaError`.**

- The best-guess `placeOrder` mutation shape was rejected by Prophet's live schema. Run `agent.py --command probe-schema` to capture the live introspection, update `scripts/prophet/orders.py` to match, and re-deploy.

**`blockers` contains `duplicate_open_order:...`.**

- The arb-bot already has an open order at this outcome+side. By design — the bot does not double-quote.
