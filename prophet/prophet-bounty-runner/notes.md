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

**Blocker (P0 to clear before §20.2 acceptance and §20.4 chore):** the
viewer query against `prophet-ai` returns `HTTP 401 Unauthorized`.
Diagnostic confirms the JWT itself is well-formed; Prophet is rejecting
the user-context query at the upstream level. Most likely cause:
`taariq@serendb.com`'s only Prophet-side history is a `Prophet Testnet
Account Live` activation email from 2026-03-25 — the mainnet
`registerWithPrivy` + `completeProfile` mutations have not run, so
mainnet has no user record bound to this Privy identity.

Two paths to clear:

- **Manual:** operator signs in once at https://app.prophetmarket.ai
  (Privy OTP + complete profile + accept ToS) before re-running the
  smoke. Lowest-risk; no further code changes.
- **Automated:** add a pre-`viewer` step that calls `registerWithPrivy`
  and (if needed) `completeProfile` from inside `acquire_token`. Real
  code, but makes the skill self-onboarding for new users. Required
  for any cron-driven autonomous user that hasn't manually onboarded.

Diagnostic stderr output is gated on `PROPHET_BOUNTY_DEBUG_LOCAL_STORAGE=1`
in the env so production cron runs do not leak token previews. Five
OTP emails were consumed during the live diagnostic loop on 2026-05-08.
