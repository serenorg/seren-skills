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

## website — seren-affiliates-website (Phase 2)

Not a Seren publisher. Plain HTTPS from the skill.

| Step | Method | Path | Notes |
|------|--------|------|-------|
| emit_unsubscribe_link | (client-side URL only) | /unsubscribe/{agent_id}/{token} | Embedded in every outbound body_template as `{unsubscribe_link}` |
| sync_remote_unsubscribes | GET | /public/unsubscribes?agent_id=...&since=... | Phase 2 dependency (serenorg/seren-affiliates-website#36); returns paginated tokens the skill joins against local `distributions` to resolve token → email |

Base URL: `https://affiliates-ui.serendb.com` (configured at `config.unsubscribe.endpoint_base` and `config.unsubscribe.sync_api_base`). No recipient PII is ever sent to this host — only HMAC tokens and `agent_id`. `seren-affiliates` (the backend) is intentionally not involved; `affiliates.serendb.com` is the Rust API surface and does not host the unsubscribe routes.
