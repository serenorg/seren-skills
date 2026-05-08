# prophet-bounty-runner — implementation notes

Tracks decisions that the plan asks the implementer to leave a breadcrumb
for, so the DRY pass after both skills package cleanly can find them.

## Phase 9 — duplicated functions from prophet-market-seeder

Per plan §15.1 (small-copy fallback), the score heuristic was copied
rather than imported. The seeder's `generate_market_candidates` is
template-driven and incompatible with the bounty runner's
PolymarketSource-driven flow, so a slim adapter was written instead of
a full sibling import.

**Duplicated (copy of the score formula):**

- `prophet/prophet-bounty-runner/scripts/candidates.py::score_candidates`
- mirrors `prophet/prophet-market-seeder/scripts/agent.py::score_market_candidates`
- same clarity / has_date / category-diversity weights (0.3 / 0.3 / 0.4)

**Bounty-runner-specific (no source-skill equivalent):**

- `candidates.generate_candidates(polymarket_sources, n)` — maps
  PolymarketSource → Candidate
- `candidates.filter_candidates(scored, submit_limit)` — score-threshold
  + submit_limit cap (the seeder filter dedups by recent titles via
  SerenDB; the bounty runner does Prophet-side dedup separately in
  Phase 10's `dedup_against_prophet` step)

**Follow-up (after Phase 11):** package `prophet-market-seeder` as an
editable package via `pyproject.toml` so siblings can `from
prophet.prophet_market_seeder.scripts.agent import
score_market_candidates`. Then collapse the duplicate. Do NOT do this
inside the bounty-runner PR — it touches the source skill.

## Phase 14 — Live test status (2026-05-08)

**Bounty:** `cad1ffb8-bc06-4842-92ab-8aea078f1d88` (`customer_slug=prophet`,
`max_pool_atomic=150_000_000`, `hold_days=90`, status `open`, escrow
funded by `taariq@serendb.com`'s SerenBucks). To grow into the
production $500 cap per plan §4, `additional_max_pool_atomic=350_000_000`
PATCH + a follow-on fund call. Pre-funding controls per plan §3 (P0
items 1, 2, 4) still need to land before that top-up.

**Live wiring landed in feat(prophet-bounty-runner):** agent.py main()
no longer prints stub_cli; it builds an HttpGateway, in-memory
storage, and calls run_command. `_cmd_setup` auto-resolves the bounty
and reports its id (plan §22 #4 satisfied). `_cmd_run` emits
`prophet_auth: {method, source, viewer_id}` and `candidates_generated`
per plan §20.2.

**Five UI/transport rotations patched during the live test:**
1. Connect-button text: `"Connect"` → `"SIGN IN"` (`SEL_CONNECT_BUTTON`).
2. Privy OTP sender: `noreply@privy.io` → `no-reply@mail.privy.io`
   (`PRIVY_OTP_SENDER`).
3. Gmail publisher path: `/users/me/messages` → `/messages?q=...` (the
   seren `gmail` publisher exposes a flat surface, not the raw Google
   Gmail path).
4. SSL: macOS Python `urlopen` did not pick up the system CA; HttpGateway
   now uses certifi-backed `ssl.create_default_context`.
5. Publisher response envelope: every Seren publisher wraps payloads
   in `{data: {body: ..., status, cost, ...}}`; HttpGateway now unwraps
   to `body` so callers see the publisher-native shape.

**OTP flow end-to-end works through JWT acquisition.** Modal opens,
email submits, Privy email arrives, gmail publisher reads it, code
extracts, code submits, JWT lands in `localStorage["privy:token"]` (413
chars, ES256, 3 segments — well-formed). `_unwrap_jwt` strips the
JSON-quote wrapping the Privy SDK adds.

**Plan §20.2 dry-run smoke is green** (10 OTP emails consumed total
across the diagnostic loop on 2026-05-08). The final run output:

```
status: ok, dry_run: true,
polymarket_sources_considered: 100, candidates_generated: 1,
prophet_markets_created: [], bounty_submission: not_attempted,
prophet_auth: { method: otp, source: otp_deferred_binding, viewer_id: "" }
```

**Critical Phase-14 discoveries (in addition to the five UI rotations
listed above):**

- **Auth channel for prophet-ai is `Cookie: privy-token=<jwt>`, not
  `Authorization: Bearer <jwt>`.** The seren publisher gateway demands
  the SerenAPIKey on `Authorization` for caller-auth/billing; the
  Privy JWT must ride on a different documented passthrough header.
  The Prophet web app's native auth is also Cookie-based, so this
  matches upstream. The plan §3 description ("user's Privy JWT rides
  on `Authorization: Bearer ...`") is incorrect for the gateway path
  — it would be correct only against a direct
  `https://app.prophetmarket.ai/api/graphql` call without the gateway.

- **`Viewer` schema does NOT have top-level `id`/`email`.** The plan
  §3 description ("OTP worker calls `Query: viewer { id email }`") is
  wrong. Real schema (introspected 2026-05-08): `Viewer { user { id
  email username }, walletBalance { availableCents totalCents
  onChainUsdc safeAddress safeDeployed }, balance { ... }, ... }`.

- **`polymarket-data` publisher returns Polymarket's native Gamma
  shape** — a flat list of market objects keyed by `conditionId`,
  `endDate`, `closed`. The original discovery module assumed
  `{sources: [...]}` with `polymarket_market_id`/`resolution_date`/
  `settled` field names. Discovery now sends an explicit deadline
  query string and tolerates both shapes.

**Two blockers remain before non-dry-run market creation works:**

1. **`viewer.user` is null.** The Cookie-authed query reaches upstream
   Prophet cleanly (no 401, no schema error), but returns
   `viewer.user = null`. Most likely the user's Privy mainnet identity
   has never been bound to a Prophet `User` record (only `Prophet
   Testnet Account Live` history exists in the inbox). Cleared by the
   operator signing in once at https://app.prophetmarket.ai
   (Privy OTP + complete profile + accept ToS).

2. **`registerWithPrivy` mutation gets `"Privy authentication
   required"` upstream**, regardless of whether the JWT is sent on
   `Authorization`, `X-Prophet-Session`, or `Cookie`. Prophet's
   mutation resolver checks auth differently from its query resolver.
   Until cleared, the autonomous self-onboarding path won't work — so
   the skill cannot recover automatically when a new user hasn't
   manually onboarded. Needs a follow-on diagnostic (likely involves
   sending multiple Privy cookies — `privy-token`, `privy-session`,
   and `privy-refresh-token` together — to mirror the web app's
   request shape exactly). Not blocking dry-run.

**Non-dry-run path still requires viewer-binding (plan §22 #12 P0).**
`require_viewer_binding=True` for non-dry-run runs. Once blocker (1)
is cleared, the non-dry-run market creation should proceed; if it
hits the mutation-auth issue (blocker 2), createMarket will return
the same "Privy authentication required" error and the run will
record `prophet.create_market_failed` events.

**The §20.4 chore commit is intentionally deferred** until at least
one non-dry-run market lands successfully on Prophet. The dry-run
smoke green is good enough for §20.2 acceptance criterion #5; the
non-dry-run market is acceptance criterion #6.
