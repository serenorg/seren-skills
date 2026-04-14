# seren-affiliate state machine

The skill operates one publisher program per run. State is owned by the
serendb database `seren_affiliate`.

## Run lifecycle

```
          ┌──────────────────────┐
          │ normalize_request    │
          └────────────┬─────────┘
                       ▼
          ┌──────────────────────┐
          │ bootstrap_auth_and_db│
          └────────────┬─────────┘
                       ▼ (3 retries, fail closed under strict_mode)
          ┌──────────────────────┐
          │ sync_affiliate_profile│ ← GET /affiliates/me, POST /affiliates on 404
          └────────────┬─────────┘
                       ▼
          ┌──────────────────────┐
          │ sync_joined_programs │ ← GET /affiliates/me/partner-links
          └────────────┬─────────┘
                       ▼
          ┌──────────────────────┐
          │ select_program       │ ← validates against joined_programs cache
          └────────────┬─────────┘
                       ▼
          ┌──────────────────────┐
          │ resolve_provider     │ ← gmail-preferred auto, else --provider
          └────────────┬─────────┘
                       ▼
          ┌──────────────────────┐
          │ ingest_contacts      │ ← pasted | gmail_contacts | outlook_contacts
          └────────────┬─────────┘
                       ▼
          ┌──────────────────────┐
          │ filter_eligible      │ ← anti-join distributions + unsubscribes
          └────────────┬─────────┘
                       ▼
          ┌──────────────────────┐
          │ enforce_daily_cap    │ ← COUNT distributions today; clip to cap
          └────────────┬─────────┘
                       ▼
          ┌──────────────────────┐
          │ draft_pitch          │ ← one seren-models call per run
          └────────────┬─────────┘
                       ▼
          ┌──────────────────────┐
          │ await_approval       │ ← blocking unless approve_draft + json_output
          └────────────┬─────────┘
                       ▼
          ┌──────────────────────┐
          │ merge_and_send       │ ← per contact: provider.messages.send
          └────────────┬─────────┘
                       ▼
          ┌──────────────────────┐
          │ persist_run_state    │ ← finalize runs row
          └────────────┬─────────┘
                       ▼
          ┌──────────────────────┐
          │ fetch_live_stats +   │ ← GET /affiliates/me/stats + /commissions
          │ render_report        │
          └──────────────────────┘
```

## Command subgraphs

| Command    | Steps |
|------------|-------|
| bootstrap  | 1–4 (stops after sync_joined_programs) |
| sync       | 1–2, 3–4, 5, render |
| ingest     | 1–2, 7, persist contacts |
| draft      | 1–10, persist_draft, render |
| send       | 1–9 validation only, 11–13 execution, render (requires existing drafts row) |
| run        | 1–15 end to end |
| status     | 1–4, 14–15 (no send) |
| block      | 1–2 + upsert into unsubscribes |

## Failure modes

- `auth_setup_required` — no Seren Desktop auth and no `SEREN_API_KEY` env var.
- `affiliate_bootstrap_failed` — 3 consecutive failures on profile or programs sync.
- `no_sender_address` — `affiliate_profile.sender_address` is empty. Bootstrap
  fails closed and returns a setup instruction.
- `no_provider_authorized` — neither gmail nor microsoft-outlook publisher is
  authorized for the caller.
- `approve_draft_without_json_output` — normalize_request rejects.
- `daily_cap_exhausted` — sent count already at cap; the run terminates cleanly
  with `sent_count=0` and a message.
