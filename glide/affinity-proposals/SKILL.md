---
name: affinity-proposals
description: Generate dry-run-first Glide proposal PDFs from Affinity CRM meeting notes, send them for review, and audit the workflow before any live CRM write-back.
---

# Glide Affinity Proposals

This skill scans an Affinity prospect list for engaged prospects with a substantive meeting note and no prior proposal note. It extracts a proposal profile, edits a bundled PowerPoint template, renders a PDF through Microsoft SharePoint/Graph, sends a review email, and records the run in SerenDB.

When `affinity.owner_emails` is set, only prospects owned by one of those emails qualify (case-insensitive); other owners' rows in the same list are skipped before their notes are fetched. Leave it empty or omit it to consider every owner in the list.

Dry-run is the default. Live mode requires both `--allow-live` and `live_mode: true` in `config.json`.

## Public Repo Rules

This repo must not contain credentials, live CRM exports, generated proposal PDFs, real prospect notes, person/list/vault IDs, or real email routing values. Use `config.example.json` placeholders only. Local `config.json`, `.env`, `out/`, generated PDFs, and `state/` are gitignored.

The template directory is reserved for customer-approved proposal templates. Unit tests use synthetic templates generated at test time.

## Setup

The skill interviews you on first run, so a non-engineer operator can finish setup without editing JSON.

1. Set `SEREN_API_KEY` in `.env` (desktop) or the deployment secret store (cloud).
2. Connect your Outlook mailbox to the `microsoft-outlook` publisher. This is the single sender mailbox both dry-run and live email go out from.
3. Connect the render account to the `microsoft-sharepoint` publisher. Any folder name works — the interview lets you pick.
4. Add your Affinity API key as an item in any Seren Passwords vault. Title it with something containing "affinity" so the interview finds it.
5. Run `python -m scripts.agent --once`. With no `config.json` present, the interview starts automatically: it asks for the Affinity list, the engaged/proposal statuses, the owner emails to filter on, which Seren Passwords vault/item holds the key, the Outlook From address, the dry-run and live CC lists, and the SharePoint folder. It writes `config.json` and runs the first dry-run.
6. Re-run setup any time with `python -m scripts.agent --setup`.

`dry_run: true` is set automatically and never asked. Going live requires the separate `--allow-live` flag plus an explicit `live_mode: true` edit (live-mode UX is a separate ticket).

### For engineers — fields the interview bakes in for the operator

| Field | Hidden value | Source |
| ----- | ------------ | ------ |
| `dry_run` | `true` | `scripts/interview.py:HIDDEN_DEFAULTS` |
| `live_mode` | `false` | same |
| `extract.model` | current production default | `scripts/extract.py:DEFAULT_MODEL` |
| `secrets.affinity_env_var` | `AFFINITY_API_KEY` | same |
| `serendb.project` | `glide-affinity-proposals` | same |
| `serendb.database` | `glide_affinity_proposals` | same |

`config.example.json` is preserved for engineer reference; the interview is the supported operator path.

## Sender Mailbox

Both dry-run and live send **from** the Seren-tenant mailbox connected to the `microsoft-outlook` publisher, via `/me/sendMail`. Connect the mailbox named in `email.sender_address` during setup — that is the single connected identity every send originates from. Before any send, the skill preflights the connection (an allowed read endpoint) and fails fast with a setup blocker if Outlook is not connected. The publisher's `default_deny` allowlist exposes no identity endpoint, so the skill cannot assert the exact connected address — connecting the correct mailbox is an operator responsibility, confirmed by the From address on the first dry-run email.

This is the interim default until MS Publisher Verification (MPN) completes for the "Seren Mail" app; sending **from a customer's own mailbox** (which requires that tenant's admin consent) is tracked separately and re-enabled after verification. Recipient routing is unchanged: dry-run goes to the dry-run recipients, live goes to the prospect owner.

## Dry-Run Behavior

In dry-run, email is routed only to the configured dry-run recipients. The live owner address is not included in `to` or `cc`. Affinity notes and status fields are never written. SerenDB audit rows are still written so the operator can inspect what would have happened.

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
