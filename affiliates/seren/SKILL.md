---
name: seren-affiliate
display-name: "Seren Affiliate Distributor"
description: "Lean partner-link distribution skill for the seren-affiliates publisher program portfolio. Operates one publisher program per run. Bootstraps the affiliate profile (registering on first run), caches joined programs in serendb, ingests contacts from a pasted list or from Gmail/Outlook address books, drafts a pitch once per run via seren-models for operator approval, sends approved copy through Gmail (preferred) or Microsoft Outlook, enforces per-program dedupe plus a global unsubscribe list, and reports local plus live conversion and commission stats from seren-affiliates."
---

# Seren Affiliate

Lean, program-agnostic partner-link distribution skill. Complement — not replacement — for the campaign-specific [`affiliates/seren-bucks`](../seren-bucks/SKILL.md) skill.

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

## Default V1 Contract

- The skill distributes **one** publisher program's partner-link per run.
- `seren-affiliates` is the source of truth for affiliate identity, partner-links, conversions, and commissions.
- The skill's own serendb database (`seren_affiliate`) is the source of truth for distribution activity, drafts, contacts, and unsubscribes.
- Contact ingestion supports **pasted** email lists and **Gmail or Outlook address books**. Nothing else in v1.
- Every run produces exactly **one** LLM-drafted pitch (via `seren-models`), subject to a single operator approval gate.
- Gmail is the **preferred** send provider when both are authorized (`provider=auto`).
- Daily cap defaults to **10** successful distributions per day, hard-capped at **25**, across all programs.
- Per-program dedupe is DB-level: a (program_slug, contact_email) pair is sent at most once, ever.
- Global unsubscribe list: one opt-out blocks all future sends across every program.
- Mandatory send footer: sender identity, physical address, and unsubscribe link.
- **One-click unsubscribe is live.** Every outbound email includes a footer link to `https://affiliates-ui.serendb.com/unsubscribe/{agent_id}/{token}`. Recipients click once to opt out. Operators can also manually block addresses via `command: block`.

## Bootstrap Order (Mandatory)

This rule overrides all other instructions and runs before any contact ingest, draft, or send:

1. Resolve auth in this order:
   - Seren Desktop injected auth (`API_KEY`)
   - `SEREN_API_KEY`
   - Fail with a setup message pointing to `https://docs.serendb.com/skills.md`.
2. Resolve or create the Seren project `affiliates`.
3. Resolve or create the Seren database `seren_affiliate` (schema in `serendb_schema.sql`).
4. Call `GET /affiliates/me`. On 404, `POST /affiliates` to register.
5. Upsert the profile into `affiliate_profile`. Fail closed if `sender_address` is empty.
6. Call `GET /affiliates/me/partner-links`. Retry up to 3 times. Upsert into `joined_programs`.
7. If affiliate bootstrap still fails, **fail closed** and do not continue.
8. Only after bootstrap succeeds may the skill select a program, ingest contacts, draft, or send.

## Capability Verification Rule

Before claiming any tool, connector, or publisher exists or does not exist, attempt to verify it by calling the relevant tool or connector.

- If the verification succeeds, proceed and say what was found.
- If it fails, say: `I checked and [tool/integration] is not available in this session.`
- Never claim Gmail, Microsoft Outlook, seren-affiliates, seren-db, or seren-models availability from memory or assumption.

## When to Use

- distribute a seren-affiliates partner link
- send my affiliate link to a contact list
- promote a publisher program via Gmail or Outlook
- check my affiliate distribution status
- register as a seren-affiliates affiliate
- unsubscribe a contact from my affiliate outreach

## Commands

All commands accept `json_output=true` for headless agent use.

| Command     | Purpose |
|-------------|---------|
| `bootstrap` | Auth, serendb project/db, profile (register on 404), joined_programs cache. Stops there. |
| `sync`      | Re-run bootstrap's profile and joined_programs refresh without continuing the pipeline. |
| `status`    | Bootstrap plus `GET /affiliates/me/stats` and `/commissions` (optional `program_slug` filter). No send. |
| `ingest`    | Bootstrap plus contact ingestion and eligibility filter. No draft, no send. |
| `draft`     | Bootstrap plus contact ingestion plus one `seren-models` draft. Stores in `drafts`. No send. |
| `send`      | Requires an existing `drafts` row. Runs the send path with the approval gate. |
| `run`       | End-to-end default: bootstrap → draft → approve → send → report. |
| `block`     | Operator-managed unsubscribe: appends `block_email` to `unsubscribes` (`source=operator_manual`). |

## Inputs

- `command` — one of the commands above. Default `run`.
- `program_slug` — required for `draft`, `send`, `run`. Must match a row in `joined_programs`. If empty in interactive mode, the skill lists available programs and asks.
- `provider` — `auto` (default, Gmail-first), `gmail`, or `outlook`.
- `contacts_source` — `pasted` (default), `gmail_contacts`, or `outlook_contacts`.
- `contacts` — for `pasted`: newline or comma delimited list, a CSV path, or a JSON array of `{email, name}`.
- `voice_notes` — optional free-text hints for the drafter.
- `approve_draft` — when `true`, skips the interactive approval gate. **Rejected unless `json_output=true` is also set** (prevents unattended sends from a human CLI).
- `daily_cap` — 1–25. Default 10.
- `json_output` — machine-readable output.
- `strict_mode` — fail closed on bootstrap failures. Default `true`.
- `block_email` — used only by `command: block`.

## State (serendb database `seren_affiliate`)

Schema in `serendb_schema.sql`. Tables:

- `affiliate_profile` — cached `/affiliates/me`.
- `joined_programs` — cached `/affiliates/me/partner-links`.
- `contacts` — deduped address universe.
- `distributions` — one row per successful send. `UNIQUE(program_slug, contact_email)` enforces per-program dedupe. Daily cap = `COUNT(*) WHERE sent_at::date = today`.
- `unsubscribes` — global opt-out list.
- `drafts` — per-run approved pitch.
- `runs` — one row per invocation.

## Compliance and Safety Rules

- `policies.dry_run_default: true` — the skill previews every batch and refuses to send unless `approve_draft=true` (with `json_output=true`) or the interactive approval is recorded.
- `policies.idempotency_required: true` — every distribution is keyed to a `run_id` plus `UNIQUE(program_slug, contact_email)`.
- Mandatory footer placeholders in every drafted body: `{name}`, `{partner_link}`, `{sender_identity}`, `{sender_address}`, `{unsubscribe_link}`. A regex gate rejects drafts missing any of them.
- Post-merge tracked-link validator (issue #404): after each per-contact merge, the skill asserts the bootstrapped `partner_link_url` substring is present in the merged body and **fails the send closed** (`validation_failed` / `tracked_link_missing`) if not. This is a defense-in-depth guard against a future LLM prompt change stripping the link or swapping a hallucinated URL.
- `sender_address` is required before any send. The skill fails closed if `affiliate_profile.sender_address` is empty.
- Hard-bounce on send auto-inserts into `unsubscribes` with `source=hard_bounce`.
- PII posture: only `name` + `email` are pulled from provider address books. Never message bodies or threads. Email addresses never appear in stdout outside the final summary, and only in structured form under `json_output=true`.

## Provider Selection

- `provider=auto` → Gmail if Gmail publisher is authorized, else Microsoft Outlook, else fail closed.
- `provider=gmail` or `provider=outlook` → explicit choice; fail closed if that one is not authorized.
- Both publishers are authorized at the Seren platform level, not inside this skill. If neither is authorized, the skill instructs the operator to authorize one via the Seren platform.

## Unsubscribe Handling

Every outbound email contains a footer link: `https://affiliates-ui.serendb.com/unsubscribe/{agent_id}/{token}`, where `token` is an HMAC of `(email, program_slug, run_id)` and `agent_id` identifies the affiliate account. Three sources feed the local `unsubscribes` table, all converging through one `persist.unsubscribes` payload on every run:

- **`link_click`.** Before every pipeline run, `sync_remote_unsubscribes` pulls from `https://affiliates-ui.serendb.com/public/unsubscribes?agent_id=X&since=T&cursor=C`. The watermarked `since` read comes from the `sync_state` table — O(1) PK lookup, no `MAX()` over `unsubscribes`. Returned `(token, unsubscribed_at)` pairs are resolved to emails via `distributions.unsubscribe_token` (UNIQUE B-tree, sub-ms per lookup). Unresolvable tokens are logged and skipped.
- **`operator_manual`.** `command: block` with `block_email=<recipient>` emits a row into `persist.unsubscribes` for the harness to upsert.
- **`hard_bounce`.** `merge_and_send` promotes bounce events into `persist.unsubscribes` with `source=hard_bounce`.

The pipeline filters `ingest.contacts` through the union of persisted `unsubscribes` plus freshly-pulled remote opt-outs before any draft or send.

**Stale-blocklist fallback.** If the public API returns 5xx or times out, `sync_remote_unsubscribes` sets `stale=True`, does not advance the watermark, and the run proceeds with whatever's already persisted. Explicit operator tradeoff: a website outage must not block affiliate campaigns, and the blocklist is at most one run stale.

`seren-affiliates` (the Rust backend) is not involved — it stores no recipient PII by design.

## Status and Stats

`command: status` and the end-of-run report join local and live state:

- Local (from serendb): counts by program — ingested, eligible, sent, skipped (dedupe), skipped (unsub), cap remaining.
- Live (from seren-affiliates): clicks, conversions, pending and paid commissions, scoped by `program_slug`.

## Related

- Sibling skill — `affiliates/seren-bucks` — campaign-specific review-first outreach for a single Seren Bucks landing page.
- `family: affiliate-v1` (shared with seren-bucks).
