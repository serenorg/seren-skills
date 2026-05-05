# Seren Bucks V1 State Machine

## Primary states

1. `bootstrap_pending`
2. `auth_ready`
3. `db_ready`
4. `affiliate_context_ready`
5. `candidate_sync_complete`
6. `proposal_ready`
7. `drafts_pending_approval`
8. `reconcile_complete`
9. `digest_ready`

## Blocking states

- `auth_setup_required`
- `affiliate_bootstrap_failed`
- `all_candidate_sources_failed`
- `dnc_blocked`

## Rules

- No transition into candidate sync occurs before `affiliate_context_ready`.
- `drafts_pending_approval` is the default terminal state for new outbound and reply batches in v1.
- `dnc_blocked` removes the candidate from future proposal states immediately.
