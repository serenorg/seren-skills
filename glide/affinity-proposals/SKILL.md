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

1. Create local `config.json` from `config.example.json` and replace every placeholder with operator-approved values.
2. Set `SEREN_API_KEY` in `.env` or the deployment secret store.
3. Complete a one-time Seren Passwords access grant for the cloud agent identity that will run this cron. The skill reads vault and item names from config; it never hardcodes IDs.
4. Connect the render account to the Microsoft SharePoint publisher and ensure the configured archive folder exists.
5. Keep `dry_run: true` until a dry-run email with a rendered PDF has been verified.

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
