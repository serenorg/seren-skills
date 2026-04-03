# SerenBucks Affiliate Outreach V1 SerenDB Schema

The v1 schema is split into five responsibilities:

1. Campaign and run control
   - `campaign_state`
   - `affiliate_runs`
2. Candidate CRM memory
   - `candidate_profiles`
   - `candidate_source_events`
3. Proposal and draft review
   - `proposal_sets`
   - `proposal_items`
   - `message_drafts`
   - `approval_events`
   - `send_batches`
4. Reply and suppression handling
   - `reply_events`
   - `dnc_events`
5. Operator summaries
   - `daily_digests`

The skill-owned SerenDB remains the CRM source of truth once a candidate has been persisted.
