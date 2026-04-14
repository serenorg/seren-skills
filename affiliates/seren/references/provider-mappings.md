# Provider mappings

The skill calls four connector publishers, each with fixed path assumptions.

## affiliates — seren-affiliates

| Step | Method | Path | Notes |
|------|--------|------|-------|
| sync_affiliate_profile | GET | /affiliates/me | 404 triggers POST /affiliates |
| register_affiliate | POST | /affiliates | First-run only |
| sync_joined_programs | GET | /affiliates/me/partner-links | 3 retries, fail closed |
| select_program (re-fetch) | GET | /affiliates/me/partner-links/{slug} | Called right before merge_and_send to avoid stale URLs |
| fetch_live_stats | GET | /affiliates/me/stats | Filter by program_slug |
| fetch_live_commissions | GET | /affiliates/me/commissions | Filter by program_slug |
| sync_remote_unsubscribes | GET | /affiliates/me/unsubscribes?since=... | Phase 2, backend dependency |

Headers: `X-Seren-Agent-Id` (cached from first `/affiliates/me` response) + `Authorization: Bearer $SEREN_API_KEY`.

## storage — seren-db

Operations: `upsert` and `get` only. Database `seren_affiliate`.

## gmail

| Step | Method | Notes |
|------|--------|-------|
| ingest_contacts | gmail.contacts.list | Pagination; name+email only |
| merge_and_send  | gmail.messages.send | Returns `message_id`; authoritative |

## outlook — microsoft-outlook

| Step | Method | Notes |
|------|--------|-------|
| ingest_contacts | outlook.contacts.list | Pagination; name+email only |
| merge_and_send  | outlook.mail.send | Returns `message_id`; authoritative |

## model — seren-models

Single call at `draft_pitch`. Prompt reference: `references/prompts/draft_pitch.md`. Output is validated for the five required placeholder tokens before persistence.
