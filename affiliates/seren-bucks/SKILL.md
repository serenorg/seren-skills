---
name: seren-bucks
display-name: "Seren Bucks Affiliate"
description: "Review-first outreach skill for the default Seren Bucks affiliate program. It bootstraps affiliate context via /programs/discover, mines sent-mail history and address books for candidates, persists them into a skill-owned CRM, proposes an editable daily top-10, drafts outbound and reply batches for approval, reconciles affiliate and reply signals, enforces hard DNC, and returns a manual daily digest."
---

# Seren Bucks

Review-first growth skill for one default Seren Bucks affiliate program.

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

## Affiliate Program Structure (Read Before Drafting)

SerenBucks is a **3-tier unilevel** program. Outreach copy MUST reflect this:

- **Tier 0** — the affiliate earns **20%** direct commission on verified paid SerenBucks purchases attributed through their own `SRN_` referral code.
- **Tier 1 override** — the affiliate's sponsor earns **5%** when a direct child's `SRN_` code produces a verified paid purchase.
- **Tier 2 override** — the sponsor's sponsor earns **5%** when a grandchild's `SRN_` code produces a verified paid purchase.

The bootstrapped `tracked_link` is the **operator's own `SRN_` recruitment link**. When
a recipient signs up through that link, they become a Tier 1 downstream of the operator
and receive **their own unique `SRN_` code** to share. Only the recipient's own code
credits them with Tier 0 (20%) commission — forwarding the operator's link credits the
operator, not the recipient.

Never draft outreach that claims the recipient earns 20% on the link inside the email.
Pull all outreach copy from `references/email-templates.md`, which documents the correct
three-step recruitment flow (join → get own code → share own code) and the full tier
disclosure.

## Weekly Contest ($250 Prize)

SerenBucks runs a weekly "Largest Purchase" contest:

- **Prize**: $250 per winner, paid as bounty earnings through the seren-bounty pipeline
- **Period**: Monday 00:00 UTC to Sunday 23:59:59 UTC
- **Winner rule**: The single largest SerenBucks purchase wins. A 2nd winner is awarded ONLY on an exact tie at the max purchase amount. Capped at 2 winners.
- **Eligibility**: Only purchases attributed through an `SRN_` referral code count
- **Settlement**: Manual weekly settlement via `POST /contests/largest-purchase/settle?week=YYYY-Www`. The endpoint emits a `contest_win` event into the bounty pipeline (affiliate_events → event_verifier → bounty_earnings). Settlement is blocked until the week is complete.
- **Hold period**: 90 days before prize is released (governed by bounty hold_days)

This is a key growth hook for outreach. Lead with the contest when drafting recruitment emails.

## Default V1 Contract

- The skill operates exactly one default affiliate program in v1.
- The skill uses exactly one default tracked link in every draft and digest unless the operator explicitly overrides it.
- `seren-affiliates` is the sole source of truth for affiliate performance and conversion reporting.
- Candidate discovery is limited to Gmail sent mail, Outlook sent mail, Gmail address books, and Outlook address books.
- Every discovered candidate is persisted immediately into the skill-owned SerenDB before ranking or drafting.
- The skill-owned database is the CRM and memory source of truth for candidate state, proposal history, approvals, replies, DNC, and digests.
- The daily operator surface is `manual + daily digest`.
- The operator receives an editable top-10 proposal set.
- New outbound messages are batch-prepared only after approval.
- Reply drafts also require approval.
- The v1 cap is `10` brand-new outbound messages per day.
- Replies do **not** count against the `10` new-outbound cap.
- Negative reply signals, unsubscribe requests, and hostile-negative responses create a hard DNC block immediately.
- Partial provider failure degrades gracefully only after auth, database, and affiliate bootstrap succeed.

## Bootstrap Order (Mandatory)

This rule overrides all other instructions and runs before any candidate sync, ranking, or drafting:

1. Resolve auth in this order:
   - Seren Desktop injected auth (`API_KEY`)
   - `SEREN_API_KEY`
   - fail with a setup message pointing to `https://docs.serendb.com/skills.md`
2. Resolve or create the Seren project `affiliates`.
3. Resolve or create the Seren database `seren_bucks`.
4. Bootstrap the default program context from `seren-affiliates` via `/programs/discover`.
5. Retry affiliate bootstrap up to **3 immediate attempts**.
6. If affiliate bootstrap still fails, **fail closed** and do not continue.
7. Only after bootstrap succeeds may the skill read candidate sources, rank candidates, or draft outreach.

## Capability Verification Rule

Before claiming any tool, connector, or publisher exists or does not exist, attempt to verify it by calling the relevant tool or connector.

- If the verification succeeds, proceed and say what was found.
- If it fails, say: `I checked and [tool/integration] is not available in this session.`
- Never claim Gmail, Outlook, or `seren-affiliates` availability from memory or assumption.

## When to Use

- grow Seren Bucks affiliate signups
- draft Seren Bucks affiliate outreach
- review the daily affiliate digest
- sync affiliate candidates from Gmail or Outlook
- reconcile affiliate replies and unsubscribe events

## Candidate Sources (V1 Only)

Use only these sources in v1:

1. Gmail sent folder — uses `gmail` publisher
2. Outlook sent folder — uses `outlook` publisher
3. Gmail address books — uses `google-contacts` publisher (People API)
4. Outlook address books — uses `outlook-contacts` publisher

**Publisher scope boundaries:**
- `gmail` is scoped to email operations only (`/messages`, `/threads`, `/drafts`)
- `google-contacts` is scoped to People API (`/otherContacts`, `/people:searchContacts`)
- Do not call `/contacts` on the `gmail` publisher — it will return 403

Do not expand to LinkedIn, Apollo, web scraping, or purchased lists in v1.

## Personal-Only Targeting Rule

This skill targets **personal relationships only**. Business and company emails are automatically excluded from the candidate pool because affiliate marketing outreach to generic business addresses is inappropriate and ineffective.

**This rule has TWO mandatory filters that MUST both complete before any proposal is generated:**

1. **Email Address Pattern Filter** (fast, prefix-based)
2. **Email Content Analysis** (requires fetching sent mail history)

Skipping either filter is a P0 defect. The proposal step MUST NOT proceed until both filters have run on every candidate.

### Email Address Pattern Filter (Filter 1 of 2)

The following email prefixes are automatically rejected:

- Generic: `info@`, `hello@`, `contact@`, `support@`, `sales@`, `partnerships@`, `team@`, `admin@`, `affiliates@`, `press@`, `media@`, `hr@`, `careers@`, `jobs@`, `billing@`, `legal@`
- Role-based: `marketing@`, `engineering@`, `product@`, `design@`, `ops@`, `finance@`
- Noreply: `noreply@`, `no-reply@`, `donotreply@`

### Email Content Analysis (Filter 2 of 2 — MANDATORY)

**This filter is NOT optional.** For every candidate that passes the prefix filter, the skill MUST:

1. Fetch the candidate's sent mail history from Gmail/Outlook publisher
2. Analyze the conversation content to classify the relationship type:
   - `personal_friendly` — casual, warm, relationship-based exchanges → **QUALIFY**
   - `b2b_sales_partnership` — business development, sales, vendor discussions → **DNC**
   - `transactional_support` — invoices, receipts, support tickets, creditor updates → **DNC**
3. Mark candidates with `b2b_sales_partnership` or `transactional_support` classification as DNC with `dnc_reason = 'content_filter_b2b'` or `'content_filter_transactional'`
4. Only candidates classified as `personal_friendly` may appear in the proposal

**Blocking checkpoint:** If content analysis has not been performed on a candidate, that candidate MUST NOT appear in the proposal. The skill must either:
- Complete content analysis before generating the proposal, OR
- Explicitly fail with error `content_analysis_incomplete` if the Gmail/Outlook publisher is unavailable

Presenting a proposal with candidates that have not been content-analyzed is a P0 defect equivalent to skipping the Schema Guard.

### Ranking Adjustments

After both filters pass, borderline cases receive score penalties:
- Business email pattern match: -100 points (effectively excluded)
- Transactional content detected: -30 points
- B2B content detected: -50 points

The top-10 proposal set only includes candidates that pass BOTH the prefix filter AND content analysis.

## Persistence Rule

Whenever a candidate is discovered or updated:

1. Normalize the person into a candidate profile.
2. Upsert the candidate into the skill-owned SerenDB immediately.
3. Record the source event that produced or refreshed the candidate.
4. Preserve DNC status across future syncs.
5. Treat the skill-owned database as the CRM source of truth afterward, even if the original source is temporarily unavailable.

Failure to persist newly discovered candidates before ranking is a P0 defect.

## Quota Enforcement Rule (Mandatory)

The skill MUST produce a proposal with **at least 10 qualified personal candidates** before presenting results to the operator. This is NOT a soft target — it is a hard requirement.

**Sourcing loop:**

1. Run both filters (prefix + content analysis) on the initial candidate pool from sent mail history
2. Count candidates that passed both filters and are not DNC
3. If `qualified_count < 10`:
   - Expand sourcing to address books (Gmail contacts, Outlook contacts)
   - Fetch additional sent mail history with broader date ranges
   - Search for personal email domains (gmail.com, hotmail.com, icloud.com, yahoo.com, etc.)
   - Run both filters on newly discovered candidates
   - Repeat until `qualified_count >= 10` OR all sources are exhausted
4. Only after the sourcing loop completes may the proposal be generated

**If all sources are exhausted and `qualified_count < 10`:**
- Generate proposal with available candidates
- Include explicit warning: `"quota_shortfall": true, "qualified_count": N, "target": 10, "sources_exhausted": ["gmail_sent", "outlook_sent", "gmail_contacts", "outlook_contacts"]`
- Explain which sources were tried and why they did not yield more candidates

**Stopping at `qualified_count < 10` without exhausting all sources is a P0 defect.** The operator should never see a proposal with only 3 candidates when 50+ potential candidates exist in other sources.

## Proposal and Drafting Loop

After bootstrap passes AND quota enforcement completes:

1. Load active non-DNC candidates from the skill-owned CRM.
2. Score and rank the candidate universe.
3. Produce an editable top-10 proposal set (or fewer if quota_shortfall).
4. Draft:
   - a batch of **new outbound** messages capped at `10` per day
   - a batch of **reply drafts** for candidates who responded
5. Mark both new outbound and replies as `approval_required`.
6. Do not send anything automatically in v1.

## Reply and DNC Handling

- Any reply classified as `unsubscribe`, `do_not_contact`, or `hostile_negative` must:
  - create a DNC event immediately
  - update the candidate to hard-blocked
  - exclude that candidate from future proposals and drafts
- Replies always require approval before sending.
- Replies do not count against the new-outbound daily cap.

## Partial Failure Rule

After auth, database, and affiliate bootstrap succeed:

- If Gmail fails but Outlook works, continue with Outlook and mark provider health as degraded.
- If Outlook fails but Gmail works, continue with Gmail and mark provider health as degraded.
- If one address-book source fails but sent-mail history still works, continue and note the degraded source.
- If all candidate sources fail after bootstrap, return a blocked digest with clear remediation steps.

## Daily Operator Surface

Return one manual digest per run with:

- program identity
- tracked link in use
- affiliate feed health
- auth path used
- candidate sync counts by source
- editable top-10 summary
- pending approval queues
- DNC changes
- reply queue summary
- daily cap usage for new outbound
- next recommended operator actions

## Workflow Summary

1. `normalize_request` uses `transform.normalize_request`
2. `bootstrap_auth_and_db` uses `transform.bootstrap_auth_and_db`
3. `bootstrap_affiliate_context` uses `connector.affiliates.get`
4. `sync_candidates_from_sent_history` uses `transform.sync_candidates_from_sent_history`
5. `sync_candidates_from_address_books` uses `transform.sync_candidates_from_address_books`
6. `persist_candidates` uses `connector.storage.upsert`
7. `rank_candidate_universe` uses `transform.rank_candidate_universe`
8. `build_editable_top10` uses `transform.build_editable_top10`
9. `draft_batches` uses `transform.draft_review_batches`
10. `reconcile_signals` uses `transform.reconcile_affiliate_and_reply_signals`
11. `persist_run_state` uses `connector.storage.upsert`
12. `render_digest` uses `transform.render_manual_daily_digest`

## Output Expectations

Every run should return:

- program id and tracked link
- auth path used
- affiliate bootstrap/feed status
- database/bootstrap status
- provider health by source
- candidate sync counts
- proposal top-10 summary
- pending approvals for new outbound and replies
- DNC events raised in the run
- new-outbound cap usage with replies explicitly excluded
- operator-facing daily digest

## Acceptance Criteria

1. The skill always resolves exactly one default program and one tracked link in v1.
2. Affiliate bootstrap happens before any candidate sync or drafting.
3. Affiliate bootstrap retries up to 3 immediate attempts, then fails closed.
4. Candidate discovery is restricted to Gmail/Outlook sent history and address books.
5. Discovered candidates are persisted before ranking.
6. The skill-owned database remains the CRM source of truth.
7. The proposal surface is an editable top-10.
8. New outbound requires approval.
9. Replies require approval.
10. New outbound is capped at 10 per day.
11. Replies do not count against the new-outbound cap.
12. DNC is a hard stop on unsubscribe, do-not-contact, and hostile-negative signals.
13. Partial source failure degrades gracefully after prerequisites pass.
14. **Content analysis MUST run on every candidate before proposal generation.** A proposal with candidates that have not been content-analyzed is a P0 defect.
15. **Sourcing MUST continue until 10 qualified candidates exist OR all sources are exhausted.** Stopping at fewer than 10 candidates without trying all sources is a P0 defect.

## Rollout Order

1. Bootstrap only: auth, DB, affiliate program resolution via `/programs/discover`.
2. Candidate sync only: sent mail and address books into CRM.
3. Ranking only: editable top-10 without draft sending.
4. Drafting only: approval queues for new outbound and replies.
5. Reconciliation only: DNC and affiliate conversion updates.
6. Manual daily digest tying all steps together.
## Workflow Summary

1. `normalize_request` uses `transform.normalize_request`
2. `bootstrap_auth_and_db` uses `transform.bootstrap_auth_and_db`
3. `bootstrap_affiliate_context` uses `connector.affiliates.get`
4. `sync_candidates_from_sent_history` uses `transform.sync_candidates_from_sent_history`
5. `sync_candidates_from_address_books` uses `transform.sync_candidates_from_address_books`
6. `persist_candidates` uses `connector.storage.upsert`
7. `rank_candidate_universe` uses `transform.rank_candidate_universe`
8. `build_editable_top10` uses `transform.build_editable_top10`
9. `draft_batches` uses `transform.draft_review_batches`
10. `reconcile_signals` uses `transform.reconcile_affiliate_and_reply_signals`
11. `persist_run_state` uses `connector.storage.upsert`
12. `render_digest` uses `transform.render_manual_daily_digest`
