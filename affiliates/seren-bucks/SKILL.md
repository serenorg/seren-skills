---
name: seren-bucks
display-name: "Seren Bucks Affiliate"
description: "Review-first outreach skill for the default Seren Bucks affiliate campaign. It bootstraps affiliate context, mines sent-mail history and address books for candidates, persists them into a skill-owned CRM, proposes an editable daily top-10, drafts outbound and reply batches for approval, reconciles affiliate and reply signals, enforces hard DNC, and returns a manual daily digest."
---

# Seren Bucks

Review-first growth skill for one default Seren Bucks affiliate campaign.

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

## Affiliate Program Structure (Read Before Drafting)

SerenBucks is a **3-tier unilevel** program. Outreach copy MUST reflect this:

- **Tier 0** — the affiliate earns **20%** direct commission on their own referrals.
- **Tier 1 override** — the affiliate's sponsor earns **5%** when a direct child refers.
- **Tier 2 override** — the sponsor's sponsor earns **5%** when a grandchild refers.

The bootstrapped `tracked_link` is the **operator's own `SRN_` recruitment link**. When
a recipient signs up through that link, they become a Tier 1 downstream of the operator
and receive **their own unique `SRN_` code** to share. Only the recipient's own code
credits them with Tier 0 (20%) commission — forwarding the operator's link credits the
operator, not the recipient.

Never draft outreach that claims the recipient earns 20% on the link inside the email.
Pull all outreach copy from `references/email-templates.md`, which documents the correct
three-step recruitment flow (join → get own code → share own code) and the full tier
disclosure.

## Default V1 Contract

- The skill operates exactly one default affiliate campaign in v1.
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
4. Bootstrap the default campaign context from `seren-affiliates`.
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

1. Gmail sent folder
2. Outlook sent folder
3. Gmail address books
4. Outlook address books

Do not expand to LinkedIn, Apollo, web scraping, or purchased lists in v1.

## Persistence Rule

Whenever a candidate is discovered or updated:

1. Normalize the person into a candidate profile.
2. Upsert the candidate into the skill-owned SerenDB immediately.
3. Record the source event that produced or refreshed the candidate.
4. Preserve DNC status across future syncs.
5. Treat the skill-owned database as the CRM source of truth afterward, even if the original source is temporarily unavailable.

Failure to persist newly discovered candidates before ranking is a P0 defect.

## Proposal and Drafting Loop

After bootstrap passes:

1. Load active non-DNC candidates from the skill-owned CRM.
2. Score and rank the candidate universe.
3. Produce an editable top-10 proposal set.
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

- campaign identity
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

- campaign id and tracked link
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

1. The skill always resolves exactly one default campaign and one tracked link in v1.
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

## Rollout Order

1. Bootstrap only: auth, DB, affiliate campaign resolution.
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
