---
name: affinity-proposals
description: Generate dry-run-first Glide proposal PDFs from Affinity CRM meeting notes, send them for review, and audit the workflow before any live CRM write-back. When invoked with no config.json, drive the first-run setup entirely as a chat interview — no menus, no terminal commands.
---

# Glide Affinity Proposals

This skill scans an Affinity prospect list for engaged prospects with a substantive meeting note and no prior proposal note. It extracts a proposal profile, edits a bundled PowerPoint template, renders a PDF through Microsoft SharePoint/Graph, sends a review email, and records the run in SerenDB.

When `affinity.owner_emails` is set, only prospects owned by one of those emails qualify (case-insensitive); other owners' rows in the same list are skipped before their notes are fetched. Leave it empty or omit it to consider every owner in the list.

Dry-run is the default. Live mode requires both `--allow-live` and `live_mode: true` in `config.json`.

## On invocation

The operator cannot use a terminal. The loading agent owns every keystroke. This section is normative.

**Fresh invocation — no `~/.config/seren/skills/affinity-proposals/config.json` on disk.** Open the very first assistant turn with the interview's intro paragraph and Question 1 (the Affinity list name). Do not offer a menu of actions. Do not propose a terminal command. Do not ask the operator to paste bash. Do not ask "what do you want to do?" first.

The agent itself runs the interview in chat. For each question:

1. Validate the answer the same way `scripts/interview.py` does (non-empty for required questions, email shape for address questions, list-comma-split for CC fields, etc.).
2. Call the live MCP tools directly when a question needs to look something up:
   - Question 5 (Affinity API key item) ranks the operator's Seren Passwords vault items via `mcp__seren-mcp__passwords_items_list` / `mcp__seren-mcp__passwords_item_get`. Reuse the same ranking rules as `rank_password_items` in `scripts/interview.py`.
   - Question 6 (Outlook sender mailbox) preflights `microsoft-outlook` via `mcp__seren-mcp__call_publisher`. If the publisher returns `OAuthRequired`, surface the connect URL and stop until the operator confirms the connection.
   - Question 10 (SharePoint folder) preflights `microsoft-sharepoint` via `mcp__seren-mcp__call_publisher` the same way.
3. After the last answer, write `~/.config/seren/skills/affinity-proposals/config.json` directly with the `Write` tool. Hidden defaults (`dry_run: true`, `live_mode: false`, `extract.model`, `secrets.affinity_env_var`, `serendb.project`, `serendb.database`) come from `scripts/interview.py:HIDDEN_DEFAULTS` — never ask the operator about them.

`python -m scripts.agent --setup` remains as an engineer fallback, but it is **never** the operator path. Do not propose it.

**Repeat invocation — `config.json` already exists, no args.** The default action is the dry-run scan. Do not show a menu; run the scan directly.

**Re-configure — operator says "re-run setup", "edit my settings", "change the list", etc.** Re-enter the same chat interview, pre-filling each question with the current value from `config.json` as the default. Only overwrite `config.json` after the operator confirms the last answer.

## Public Repo Rules

This repo must not contain credentials, live CRM exports, generated proposal PDFs, real prospect notes, person/list/vault IDs, or real email routing values. Use `config.example.json` placeholders only. Local `config.json`, `.env`, `out/`, generated PDFs, and `state/` are gitignored.

The template directory is reserved for customer-approved proposal templates. Unit tests use synthetic templates generated at test time.

## Setup

The agent interviews the operator in chat on first run. The operator does not need to touch JSON, a terminal, or a CLI flag. The prerequisite live connections the interview will confirm:

- `SEREN_API_KEY` available to the runtime (`.env` for desktop, deployment secret for cloud).
- Outlook mailbox connected to the `microsoft-outlook` publisher. This is the single sender mailbox both dry-run and live email go out from.
- Render account connected to the `microsoft-sharepoint` publisher. Any folder name works — the interview lets the operator pick.
- Affinity API key stored as a Seren Passwords vault item. Title it with something containing "affinity" so the interview's ranking finds it first.

`dry_run: true` is set automatically and never asked. Going live requires the separate `--allow-live` flag plus an explicit `live_mode: true` edit (live-mode UX is a separate ticket).

## For engineers

`scripts/interview.py` is the reference implementation of the chat flow; the loading agent should mirror its question order and validation rules. The CLI entrypoint is preserved for engineer use only:

```bash
python -m scripts.agent --once      # first run with no config.json
python -m scripts.agent --setup     # re-enter the interview from a terminal
```

These commands are NOT for the operator. The loading agent must never print them as instructions in chat.

### Fields the interview bakes in for the operator

| Field | Hidden value | Source |
| ----- | ------------ | ------ |
| `dry_run` | `true` | `scripts/interview.py:HIDDEN_DEFAULTS` |
| `live_mode` | `false` | same |
| `extract.model` | current production default | `scripts/extract.py:DEFAULT_MODEL` |
| `secrets.affinity_env_var` | `AFFINITY_API_KEY` | same |
| `serendb.project` | `glide-affinity-proposals` | same |
| `serendb.database` | `glide_affinity_proposals` | same |

`config.example.json` is preserved for engineer reference; the chat interview is the supported operator path.

## Sender Mailbox

Both dry-run and live send **from** the Seren-tenant mailbox connected to the `microsoft-outlook` publisher, via `/me/sendMail`. Connect the mailbox named in `email.sender_address` during setup — that is the single connected identity every send originates from. Before any send, the skill preflights the connection (an allowed read endpoint) and fails fast with a setup blocker if Outlook is not connected. The publisher's `default_deny` allowlist exposes no identity endpoint, so the skill cannot assert the exact connected address — connecting the correct mailbox is an operator responsibility, confirmed by the From address on the first dry-run email.

This is the interim default until MS Publisher Verification (MPN) completes for the "Seren Mail" app; sending **from a customer's own mailbox** (which requires that tenant's admin consent) is tracked separately and re-enabled after verification. Recipient routing is unchanged: dry-run goes to the dry-run recipients, live goes to the prospect owner.

## Dry-Run Behavior

In dry-run, email is routed only to the configured dry-run recipients. The live owner address is not included in `to` or `cc`. Affinity notes and status fields are never written. SerenDB audit rows are still written so the operator can inspect what would have happened.

### Troubleshooting Affinity Notes

The scanner reads notes from both the Affinity organization and linked person records. If an organization qualifies by list, status, and owner but Affinity returns zero org notes and zero linked-person notes, the dry-run records `skipped.no_notes_via_api` and logs `prospect_skipped_no_notes_via_api` with a `likely_cause`.

Affinity's API respects in-product sharing scope. If the Affinity UI shows notes but this command returns an empty result, the API key's user probably cannot access those notes/list/space, or exports are restricted to Admins:

```bash
curl -u :{key} 'https://api.affinity.co/notes?organization_id={id}'
```

Use an Admin-level Affinity API key or grant the API key's user access to the affected list and notes in Affinity Settings, then rerun the dry-run.

## Live Behavior

Live mode is blocked unless both controls are set:

```bash
python -m scripts.agent --allow-live --once
```

and:

```json
{ "live_mode": true }
```

After a successful live send, the skill writes an Affinity note and advances the status to the configured proposal status. Do not run this without customer approval.

## Functional Verification

For each PR, run the critical unit tests and then the highest available dry-run check:

```bash
python -m pytest glide/affinity-proposals/tests
python -m scripts.secrets --selfcheck --config config.json
python -m scripts.affinity --scan --config config.json
python -m scripts.agent --once --config config.json
```

If a live provider blocks the full dry-run, create a `bug` issue assigned to the operator, link it from the PR, and record the exact failed provider call and status.

The SharePoint renderer runs a preflight against `microsoft-sharepoint` before upload. If the publisher returns `OAuthRequired`, connect the render account to the Microsoft provider and rerun the dry-run; the skill will stop before generating partial artifacts.

The secret resolver requires the hosted Seren Passwords tools that return plaintext vault and item metadata after access is granted. If only encrypted REST records are available, the skill stops with a setup blocker before making any Affinity calls.

## Cloud Deployment

Deploy as a seren-cloud cron at 10:00 `America/Chicago` on the default Python runtime. Do not use Docker, a custom image, or a local pull runner.
