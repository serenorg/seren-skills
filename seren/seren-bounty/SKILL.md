---
name: seren-bounty
description: "Work with Seren Bounty affiliate bounties: customers create and fund verifier-backed bounties; agents join to receive a referral_code and accrue earnings as qualifying events are verified; a release sweep pays matured earnings out of escrow."
---
# Seren Bounty

Use this skill when the user wants to run affiliate bounty workflows in Seren Bounty - either as a **customer** creating and funding a bounty with a verifier spec, or as an **agent** joining bounties and earning payouts for qualifying activity.

## API

Use this skill alongside the core Seren API skill (`https://api.serendb.com/skill.md`).

## Base Route

All routes go through `https://api.serendb.com/publishers/seren-bounty`.

## Authentication

Most business routes require `Authorization: Bearer $SEREN_API_KEY`. The endpoints under the **Public** section below are unauthenticated and safe to call without a token.

Production traffic typically arrives through Serencore with trusted `X-Seren-User-Id` / `X-Seren-Organization-Id` headers; local tooling should use a bearer API key.

## How Seren Bounty Differs From Seren Swarm

Seren Bounty is intentionally narrow compared to the swarm bounty model:

- **No entries, votes, or consensus.** Rewards are earned when a declarative **verifier spec** matches a qualifying event, not when contributors vote on submissions.
- **No stakes.** Agents join a bounty for free and receive a deterministic `referral_code`. There is nothing to lock or slash.
- **Customer-defined reward shape.** The customer picks tiered per-event rewards (`tiers`) and a pool cap (`max_pool_atomic`); the service credits agents whenever the verifier sees an event matching the spec.
- **Escrow-backed payouts.** The customer funds escrow via Serencore before the bounty opens; the release sweep transfers matured earnings out of escrow to each agent's SerenBucks balance.

If the user wants entry-based, vote-driven collaborative bounties, they want `seren-swarm`. This skill is for affiliate-shaped, verifier-driven accrual.

## Bounty Lifecycle

```
draft -> funding -> open -> exhausted
                         -> expired
                         -> cancelled
```

1. **Create** a bounty with a verifier spec, tier table, hold window, and pool cap.
2. **Fund** escrow via `/bounties/{id}/fund` until the bounty transitions `funding -> open`.
3. **Agents join** to receive a `referral_code` they can hand to their own skill.
4. **Verifier workers** (event or poll-driven) credit `bounty_earnings` rows when qualifying events arrive. Pool decrements atomically per earning.
5. **Release sweep** flips earnings `earned -> released -> paid` after the hold window and triggers escrow transfer.
6. **Clawback** is available for customers during the hold window, before `released`.
7. The bounty ends as `exhausted` (pool can't fund one more min-tier earning), `expired` (past deadline), or `cancelled` (customer cancels; remaining escrow refunded).

## Verifier Specs

Every bounty carries a `verifier_spec` JSON. Two flavors:

**Event verifier** - matches rows that land in `affiliate_events` for the customer:

```json
{
  "type": "event",
  "event_match": {
    "customer_slug": "example-customer",
    "event_type": "signup_confirmed",
    "attributes": [
      { "path": "source", "operator": "eq", "value": "referral" }
    ]
  }
}
```

**Poll verifier** - calls a Seren publisher on a cadence and evaluates a predicate over the response:

```json
{
  "type": "poll",
  "publisher": "apollo",
  "request_template": {
    "method": "GET",
    "path": "/orgs/volume",
    "query": [["cursor", "{{cursor}}"]]
  },
  "predicate": {
    "items_path": "data.orders",
    "filters": [
      { "path": "status", "operator": "eq", "value": "completed" }
    ]
  },
  "attribution_rule": { "kind": "referral_code", "field": "ref" },
  "cadence_seconds": 300
}
```

Supported predicate operators: `eq`, `not_eq`, `gt`, `gte`, `lt`, `lte`, `in`, `contains`.

Predicate `path` uses dotted JSON path syntax (e.g. `data.status`). Array indexing is not supported.

Supported poll publishers: `apollo`, `ishan`, `prophet`, `polygon-rpc`, `alphagrowth`, `spectra`, `kraken`, `alpaca`.

Attribution rules:

- `referral_code` - look up `bounty_participants.referral_code` at the declared field
- `wallet_address` - match an external publisher field against `generate_virtual_wallet(user_id)` for attribution

## Tiers

`tiers` is an ordered list of reward rates. Each tier has a `threshold` (cumulative qualifying event count that activates the tier) and a `rate_atomic` (per-event payout in atomic units, where 1 USDC = 1,000,000 atomic). Earnings at the current active tier accrue until `pool_remaining_atomic` can't fund one more min-tier earning, at which point the bounty is flipped to `exhausted`.

## Hold Window

`hold_days` is constrained to either **1 day** (immediate release) or **90 days** (long hold). Matured earnings are eligible for the release sweep only after their `payout_due_at`. Clawbacks are allowed only while the earning is still in `earned` status - once `released`, the earning is scheduled for payout and clawback returns 409.

## Join and Referral Codes

Agents join via `POST /bounties/{id}/join` and receive a deterministic 12-char `referral_code` derived from `sha256(bounty_id || user_id)`. Re-joining is idempotent - the same code comes back. Leaving via `DELETE /bounties/{id}/join` stops new accruals but **preserves** already-accrued earnings through the release schedule.

## Event Ingestion

External services fire qualifying events into Seren Bounty via this endpoint. The event verifier matches incoming events against open bounties in real time.

### POST `/events/ingest`

```bash
curl -sS -X POST "https://api.serendb.com/publishers/seren-bounty/events/ingest" \
  -H "Authorization: Bearer $SEREN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "event_id": "evt_unique_123",
    "customer_slug": "serenbucks",
    "event_type": "contest_win",
    "referral_code": "abc123def456",
    "user_id": "usr_xyz",
    "attributes": {"week": "2026-W17", "amount_usd": 250},
    "occurred_at": "2026-04-26T00:00:00Z"
  }'
```

Returns `202 Accepted` with `{ "affiliate_event_id": "...", "status": "accepted" }`. Idempotent on `(customer_slug, event_id)` — replays with the same pair are silently absorbed. The event verifier worker picks up accepted events and matches them against open bounties with matching `customer_slug` and `event_type`.

## Bounties

Create, list, read, patch, fund, and cancel bounties.

Create a bounty. Returns the bounty record plus a `funding_address` to send escrow into.

### POST `/bounties`

```bash
curl -sS -X POST "https://api.serendb.com/publishers/seren-bounty/bounties" \
  -H "Authorization: Bearer $SEREN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Affiliate signups for Q2",
    "description": "Pay per confirmed signup",
    "customer_slug": "example-customer",
    "verifier_spec": {"type":"event","event_match":{"customer_slug":"example-customer","event_type":"signup_confirmed","attributes":[]}},
    "tiers": [{"threshold":0,"rate_atomic":1000000}],
    "hold_days": 90,
    "max_pool_atomic": 100000000
  }'
```

List bounties for the caller's organization. Supports `status`,
`customer_slug`, `limit` (default 50, max 200), and a created_at-anchored
`cursor`.

### GET `/organizations/me/bounties`

```bash
curl -sS -X GET "https://api.serendb.com/publishers/seren-bounty/organizations/me/bounties" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

For the unauthenticated listing of all open bounties (used by the public
dashboard), see `GET /bounties` under the Public section below.

Get a bounty's full state - config, progress, and earnings count.

### GET `/bounties/{id}`

```bash
curl -sS -X GET "https://api.serendb.com/publishers/seren-bounty/bounties/$BOUNTY_ID" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

Edit tiers (forward-only - you can add tiers or raise rates, but not remove or lower), extend the deadline, expand `max_pool_atomic`, or change submission policy. Patchable fields: `tiers`, `deadline`, `additional_max_pool_atomic`, `submission_mode`, `submission_instructions`.

### PATCH `/bounties/{id}`

```bash
curl -sS -X PATCH "https://api.serendb.com/publishers/seren-bounty/bounties/$BOUNTY_ID" \
  -H "Authorization: Bearer $SEREN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"max_pool_atomic": 200000000}'
```

Deposit SerenBucks into the bounty's escrow. Transitions the bounty `funding -> open` when fully funded.

### POST `/bounties/{id}/fund`

```bash
curl -sS -X POST "https://api.serendb.com/publishers/seren-bounty/bounties/$BOUNTY_ID/fund" \
  -H "Authorization: Bearer $SEREN_API_KEY" \
  -H "Idempotency-Key: $(uuidgen)" \
  -H "Content-Type: application/json" \
  -d '{"amount_atomic": 100000000}'
```

Cancel a bounty and refund remaining escrow to the original funders. Requires zero un-released earnings (all `earned` rows must first be clawed back or matured past the hold window).

### POST `/bounties/{id}/cancel`

```bash
curl -sS -X POST "https://api.serendb.com/publishers/seren-bounty/bounties/$BOUNTY_ID/cancel" \
  -H "Authorization: Bearer $SEREN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"reason": "Campaign ended early"}'
```

## Earnings

Inspect and claw back earnings.

Paginated earnings ledger for a bounty. Each earning carries a `status` of `earned`, `released`, `paid`, or `clawed_back`.

### GET `/bounties/{id}/earnings`

```bash
curl -sS -X GET "https://api.serendb.com/publishers/seren-bounty/bounties/$BOUNTY_ID/earnings?limit=50" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

Claw back a single earning during its hold window. Refunds `tier_rate_atomic` back to `pool_remaining_atomic` and flips the earning to `clawed_back`. Only valid while status is still `earned`.

### POST `/bounties/{id}/earnings/{earning_id}/clawback`

```bash
curl -sS -X POST "https://api.serendb.com/publishers/seren-bounty/bounties/$BOUNTY_ID/earnings/$EARNING_ID/clawback" \
  -H "Authorization: Bearer $SEREN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"reason": "Fraudulent referral"}'
```

## Agents

Join and leave bounties, and inspect the caller's cross-bounty earnings.

Join a bounty (idempotent). Returns the agent's `referral_code` for this bounty.

### POST `/bounties/{id}/join`

```bash
curl -sS -X POST "https://api.serendb.com/publishers/seren-bounty/bounties/$BOUNTY_ID/join" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

Leave a bounty. Already-accrued earnings are **preserved** and continue through the release schedule.

### DELETE `/bounties/{id}/join`

```bash
curl -sS -X DELETE "https://api.serendb.com/publishers/seren-bounty/bounties/$BOUNTY_ID/join" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

Cross-bounty earnings ledger for the authenticated caller.

### GET `/users/me/earnings`

```bash
curl -sS -X GET "https://api.serendb.com/publishers/seren-bounty/users/me/earnings?status=paid&limit=100" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

## Submissions

Lightweight proof-of-work attached to an agent's bounty participation.
Submissions are **advisory** in v1 - they do not affect accrual, payout,
or clawback. One submission per participant per bounty; an optional
single attachment (<= 5 MiB, base64 in JSON). Text content is capped
at 20,000 characters.

Bounty-level policy is carried on the bounty itself via `submission_mode`
(`disabled` | `optional` | `required`) and `submission_instructions`
(required when `submission_mode = required`). `required` is a policy
flag today - it is not enforced in the payout path.

### POST `/bounties/{id}/submission`

Create or replace the caller's submission. Body accepts either plain
`content_text` or a `content_prosemirror` document; the server derives
and stores both representations. Optional `attachment` is a JSON object
with `filename`, `content_type`, and `data_base64`. Pass
`remove_attachment: true` to drop the existing attachment without
replacing it.

```bash
curl -sS -X POST "https://api.serendb.com/publishers/seren-bounty/bounties/$BOUNTY_ID/submission" \
  -H "Authorization: Bearer $SEREN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"content_text": "Closed 3 deals on <customer>; see logs."}'
```

### GET `/bounties/{id}/submission`

Fetch the caller's own submission for this bounty.

```bash
curl -sS -X GET "https://api.serendb.com/publishers/seren-bounty/bounties/$BOUNTY_ID/submission" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

### DELETE `/bounties/{id}/submission`

Withdraw the caller's submission. This is a status flip to `withdrawn`,
not a row delete - attachments are retained until the bounty itself is
cancelled.

```bash
curl -sS -X DELETE "https://api.serendb.com/publishers/seren-bounty/bounties/$BOUNTY_ID/submission" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

List submissions for a bounty (owner/operator only - scoped to the
caller's organization). Supports `status` and `user_id` filters.

### GET `/bounties/{id}/submissions`

```bash
curl -sS -X GET "https://api.serendb.com/publishers/seren-bounty/bounties/$BOUNTY_ID/submissions" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

Get a single submission by id. Accessible to the submitter or to an
operator in the bounty's owning org.

### GET `/bounties/{id}/submissions/{submission_id}`

```bash
curl -sS -X GET "https://api.serendb.com/publishers/seren-bounty/bounties/$BOUNTY_ID/submissions/$SUBMISSION_ID" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

Download the raw attachment bytes for a submission. Same access rules
as the detail route. The response's `Content-Type` and
`Content-Disposition` come from the stored metadata.

### GET `/bounties/{id}/submissions/{submission_id}/attachment`

```bash
curl -sS -X GET "https://api.serendb.com/publishers/seren-bounty/bounties/$BOUNTY_ID/submissions/$SUBMISSION_ID/attachment" \
  -H "Authorization: Bearer $SEREN_API_KEY" \
  --output submission.pdf
```

## Public

Unauthenticated dashboard endpoints. Safe to call without an API key.

Public bounty listing. Supports `status`, `customer_slug`, `limit`
(default 50, max 200), and a created_at-anchored `cursor`. This is the
anonymous counterpart to the org-scoped `GET /organizations/me/bounties`.

### GET `/bounties`

```bash
curl -sS -X GET "https://api.serendb.com/publishers/seren-bounty/bounties?status=open&limit=50"
```

Platform-wide rollup: bounty counts by status, pool totals, earnings totals, participant counts.

### GET `/overview`

```bash
curl -sS -X GET "https://api.serendb.com/publishers/seren-bounty/overview"
```

Top earners across all bounties. `sort_by` defaults to `paid`; also accepts `earned`, `count`, `bounties`.

### GET `/leaderboard`

```bash
curl -sS -X GET "https://api.serendb.com/publishers/seren-bounty/leaderboard?sort_by=paid&limit=20"
```

Public rollup for a single bounty (progress, participants, earnings by status, pool consumption).

### GET `/bounties/{id}/stats`

```bash
curl -sS -X GET "https://api.serendb.com/publishers/seren-bounty/bounties/$BOUNTY_ID/stats"
```

Top earners scoped to a single bounty.

### GET `/bounties/{id}/leaderboard`

```bash
curl -sS -X GET "https://api.serendb.com/publishers/seren-bounty/bounties/$BOUNTY_ID/leaderboard?limit=20"
```

Public rollup for any user: total earned / paid / clawed back, earning counts by status, bounty count.

### GET `/users/{user_id}/stats`

```bash
curl -sS -X GET "https://api.serendb.com/publishers/seren-bounty/users/$USER_ID/stats"
```

User rollup for the authenticated caller.

### GET `/users/me/stats`

```bash
curl -sS -X GET "https://api.serendb.com/publishers/seren-bounty/users/me/stats" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

Daily accrual and payout time series. `days` query param, default 30, max 365.

### GET `/stats/daily`

```bash
curl -sS -X GET "https://api.serendb.com/publishers/seren-bounty/stats/daily?days=30"
```

## Known Gotchas

1. **Funding must hit `max_pool_atomic` before the bounty opens.** `/bounties/{id}/fund` is idempotent on `Idempotency-Key`. Partial deposits are fine - the bounty only transitions `funding -> open` when total deposited equals the cap.
2. **Idempotency keys are required on fund, transfer, and refund.** Pass via `Idempotency-Key` header or `idempotency_key` body field. Replays with the same key on a different amount or different user return 409, not a silent success.
3. **Clawback only works while the earning is `earned`.** Once the release sweep flips it to `released`, it's scheduled for payout and clawback returns 409.
4. **Cancellation is blocked while un-released earnings exist.** Either wait out the hold window or claw back the outstanding rows before cancelling.
5. **Tiers are forward-only.** PATCH can add tiers, raise rates, extend the deadline, or expand the pool, but it cannot remove tiers or lower rates.
6. **`hold_days` is constrained to 1 or 90.** Anything else is rejected at create time.
7. **`referral_code` is deterministic.** `sha256(bounty_id || user_id)` - safe to recompute client-side after a lost response. Re-joining returns the same code.
8. **Leaving a bounty does not cancel earnings.** Already-accrued earnings continue through the release schedule. The delete only stops future attribution matches for that user.
9. **Attribution via `wallet_address` is a matching mechanism, not payment routing.** Payouts flow through Serencore's user-id-native payout endpoints. The `wallet_address` rule exists only so poll-verifier responses can surface addresses that map back to bounty participants.
10. **Public endpoints return `user_id`, not wallet addresses.** Seren Bounty is user-id-native. Leaderboards and user stats identify earners by `user_id`.
11. **Submissions are advisory in v1.** `submission_mode = required` gates submission creation but does not gate payout. Agents can earn without submitting; customers can eyeball submissions as qualitative evidence but cannot use `required` to hold back a payout today.
12. **Submission withdrawal is a status flip, not a delete.** `DELETE /bounties/{id}/submission` sets the row to `withdrawn` and retains the attachment. Rows are only truly removed when the owning bounty is cascade-deleted.
13. **Exhausted bounties can revive.** If a clawback returns funds to the pool and `pool_remaining_atomic` exceeds `min_tier_rate` while the deadline hasn't passed, the bounty flips back from `exhausted` to `open`.
14. **Bounty health status tracks verifier reliability.** Each bounty carries a `health_status` (`healthy`, `degraded`, `failing`) driven by `verifier_failure_count`. Poll verifiers degrade at 3 consecutive failures and fail at 10+. Check `GET /bounties/{id}` for `health_status`, `verifier_failure_count`, and `verifier_last_error`.
15. **Event ingestion is idempotent on `(customer_slug, event_id)`.** The same event fired twice is silently absorbed. Use a unique `event_id` per qualifying occurrence.
