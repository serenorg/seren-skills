---
name: pk-lead-intelligence
description: Daily Packaging-division lead enrichment + weekly status doc for Salesforce. Reads PK Leads, researches each one, writes a structured Note to the Lead's Related tab, and publishes a Tuesday status doc to Google Drive. Headless Salesforce UI automation only — no Salesforce REST/Connected-App use.
---

# pk-lead-intelligence

PK Lead Intelligence is the autumn customer skill that runs the daily
enrichment + weekly status pipeline for the Packaging (PK) division.
It signs into Salesforce as the human owner via the org's standard
Microsoft SSO + TOTP flow, reads PK Leads, researches each one, writes
a structured Note back to the Lead's Related tab, and on Tuesday
mornings publishes a weekly status doc to Google Drive.

The skill drives the Salesforce Lightning UI through Playwright. It
does **not** use a Salesforce Connected App, the REST API, SOQL, or
Apex. Authentication is the same path a person uses.

This document is the operator handoff. Read it end-to-end before
running the skill.

## Status by Phase

The skill ships in phases. Selector verification against HU's live
Lightning landed on 2026-05-21 (issue #563); Phases 3/4 are now
end-to-end against the production org, not stubbed.

| Phase | Status | What is real |
| :--- | :--- | :--- |
| 1 — auth + storage | ✅ live | Microsoft SSO + 1Password Service Account; storage-state reuse |
| 2 — enrichment dry-run | ✅ live | Lead fetch, Perplexity + Claude + LinkedIn research, `.docx` render |
| 3 — reporting validation | ✅ live (2026-05-21) | Validate-only navigation to three operator-owned artifacts: `All Sources PK Leads` report (`00OS700000IzEBlMAN`), `PK Inbound Web Lead and Activity Tracking - SerenAI` dashboard (`01ZS7000004KhcnMAC`), `PK Inbound Web Lead and Opportunity Tracking - SerenAI` dashboard (`01ZS7000004KhePMAS`). Spec contracts unit-tested. |
| 4 — live Note write | ✅ live (2026-05-21) | Per-Lead Project Business Unit DOM read (cross-division gate); SerenDB `pk_lead_enrichment_log` ledger (24h recency); Quill-editor Note-form driver; load-bearing write-then-stamp order; `--allow-live` × `live_mode=true` dual gate; weekly doc renderer + Drive share. |
| 5 — cron + slash command + monitoring | ❌ not started | seren-cron jobs, local-pull runner, `/pk-status`, JSON envelope, failure-modes doc |

### Architectural notes (issue #563 closeout)

Three pieces that shipped differently than the original Phase 3/4
spec, all because the operator's Salesforce permission set in HU is
constrained to a regular-user role (no Setup access):

1. **Custom Lead fields were not created.** `PACKAGING__c`,
   `Last_Enrichment_At__c`, and `Activity_Gap_Days__c` from the
   original spec do not exist and never will. The cross-division
   gate reads HU's existing `Project Business Unit` field instead
   (value `PACKAGING` for the PK division). Recency moved to a
   SerenDB-owned `pk_lead_enrichment_log` table because Salesforce
   does not need to know when the skill last touched a Lead.

2. **Reports + dashboards are operator-owned, not skill-created.**
   The Lightning Report Builder and Dashboard Builder live inside
   Aura-app iframes that cannot be cleanly driven every cron tick.
   The three artifacts above were cloned manually by Nathan; the
   skill validates each is still reachable on every provision tick
   but does not edit them.

3. **Per-Lead detail-page read for the division gate.** The original
   spec assumed the All Sources PK Leads report would surface
   `PACKAGING__c` as a column the cron could read from the list
   view. With the field gone, the cron now navigates to each Lead's
   detail page to read `Project Business Unit` directly. One extra
   page-load per Lead per cycle is acceptable at the skill's volume.

### How to tell what state you are in

- `--command run --dry-run` — works end-to-end against a real org login. Produces a `.docx` of the rendered Note for the first matching Lead and exits.
- `--command run --allow-live` — requires `inputs.live_mode: true` AND `inputs.serendb_connection_uri` set in `config.json`. Live runs populate `is_packaging` from the Lead detail page, then enforce the 24h recency gate via the SerenDB ledger, then drive the Note form on PK Leads only.
- `--command provision --allow-live` — navigates to each of the three pinned artifact URLs and confirms they load under the operator's session. Does not edit them.
- `--command weekly` — renders the weekly Google Doc and uploads + shares it.

## When to Use

- daily PK lead enrichment cron (runs on weekdays at 06:00 in the
  org's local timezone)
- weekly PK status doc generation (runs Tuesday mornings)
- ad-hoc backfill on a fresh window of recent PK Leads
- manual `/pk-status` slash command to read this week's status doc
- diagnostic re-runs after a Salesforce / Microsoft Authenticator
  outage to clear backlog without duplicating Notes

Do not use this skill to write into divisions other than Packaging.
Mis-routed enrichments are a P0 defect — the PK / PL / MD / NW split
exists in the source data and the skill respects it.

## Setup

### Prerequisites

- Python 3.11+ on the always-on host that will run cron.
- Read access to the org's Salesforce production tenant for the
  named human owner.
- Access to a 1Password Service Account that can read the SF login
  item.
- A Seren account with `SEREN_API_KEY` and enough SerenBucks to cover
  daily Perplexity + Claude calls (~$0.25/run today).
- A Google Drive folder ID where the weekly status doc should land,
  and an email to share each new doc with.

### 1Password Service Account

The Salesforce credentials (username, password, rolling TOTP) live in
a 1Password vault. The skill reads them at runtime via the `op` CLI
under a Service Account token.

1. In 1Password admin, create a vault named `PK Salesforce Skill`
   and add one login item named `PK Salesforce`. The item must carry
   `username`, `password`, and a TOTP field.
2. Create a Service Account scoped to **read-only** access on that
   vault. Generate its token.
3. On the host that will run the cron, install the `op` CLI:
   `brew install --cask 1password-cli` (or the Linux package).
4. Verify: `op --version` returns 2.x.
5. Set `OP_SERVICE_ACCOUNT_TOKEN` in `.env` (see Configuration).
6. Sanity-check from the shell:
   - `op vault list` must list `PK Salesforce Skill`.
   - `op item get "PK Salesforce" --vault "PK Salesforce Skill"
     --otp` must print a rolling 6-digit code.

Never paste the Service Account token into chat or commit it. The
`.gitignore` blocks `.env`, but the token is the most sensitive
credential in this skill — protect it like a production password.

### Install

```bash
cd autumn/pk-lead-intelligence
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env       # fill in
cp config.example.json config.json   # adjust
```

## Configuration

### `.env`

Every variable in `.env.example` is required. The file is
gitignored.

| Variable | Source | Notes |
| :--- | :--- | :--- |
| `SEREN_API_KEY` | Seren Desktop or `https://docs.serendb.com/skills.md` | Used for Perplexity + Claude + Google Drive publisher calls. |
| `OP_SERVICE_ACCOUNT_TOKEN` | 1Password admin | Read-only SA scoped to the SF vault. |
| `OP_VAULT` | Hardcoded to `PK Salesforce Skill` | Rename only if the vault is renamed. |
| `OP_ITEM` | Hardcoded to `PK Salesforce` | Rename only if the item is renamed. |

### `config.json`

The example in `config.example.json` ships with all required keys.
The fields the operator typically edits:

| Field | Default | Notes |
| :--- | :--- | :--- |
| `inputs.salesforce_org_url` | `https://<org>.lightning.force.com` | Replace with the live org URL. |
| `inputs.salesforce_owner_email` | empty | The Microsoft / SSO email the skill signs in as. |
| `inputs.live_mode` | `false` | Defense-in-depth. Salesforce writes also require `--allow-live` on the CLI. |
| `inputs.monthly_close_target_usd` | `500000` | Drives the rolling-forecast pacing math. Adjust quarterly. |
| `inputs.google_drive_folder_id` | empty | Where the weekly doc lands. |
| `inputs.nathan_share_email` | empty | Who the weekly doc is auto-shared with. |
| `schedule.daily_cron` | `0 6 * * 1-5` | Weekdays at 06:00 in `schedule.timezone`. |
| `schedule.weekly_cron` | `0 7 * * 2` | Tuesday at 07:00. |
| `schedule.timezone` | `America/New_York` | Operator-local. |
| `limits.max_leads_per_daily_run` | `50` | Hard cap. The skill will not enrich more than this even if the report returns more rows. |
| `perplexity.*` / `claude.*` | sensible defaults | Override only if a model is deprecated. |

The populated `config.json` is gitignored — only `config.example.json`
is committed.

## Run

### Dry-run (no Salesforce writes)

The default and the only path that should run while the operator is
still validating Note quality.

```bash
python scripts/agent.py --command run --dry-run
```

Outputs a local `.docx` of the rendered Note for the first matching
Lead and exits. Re-run on a fresh Lead until the Note format passes
operator review.

### Live single-shot

After `live_mode=true` is set in `config.json` **and** the operator
has reviewed at least five dry-run Notes:

```bash
python scripts/agent.py --command run --allow-live
```

Both `live_mode=true` and `--allow-live` are required. Either alone
refuses to write. This is intentional — see Pre-Run Checklist below.

### Weekly status doc

The weekly doc is normally cron-driven, but a manual run is fine:

```bash
python scripts/agent.py --command weekly
```

It refuses to run unless `live_mode=true` because the doc references
real, written Notes.

### Slash command

Inside a Seren Desktop chat:

```
/pk-status
```

Returns the doc URL and the executive summary for the most recent
weekly run. If no doc exists this week yet, offers to trigger an
on-demand `--command weekly` run.

## Schedule

The skill runs on seren-cron with these defaults (timezone:
`America/New_York`):

| Job | Cron | What it does |
| :--- | :--- | :--- |
| `pk-lead-intelligence-daily` | `0 6 * * 1-5` | Enrich up to `max_leads_per_daily_run` PK Leads; write Notes if live. |
| `pk-lead-intelligence-weekly` | `0 7 * * 2` | Generate the weekly status doc and share it. |

The schedule lives in seren-cron; a long-lived local pull runner on
the always-on host claims due ticks. To register and start the
runner:

```bash
python scripts/setup_cron.py create --config config.json
python scripts/run_local_pull_runner.py --config config.json
```

Leave the runner process alive on the always-on host. Closing it is
equivalent to pausing the cron.

To pause or resume:

```bash
python scripts/setup_cron.py list
python scripts/setup_cron.py pause --job-id <id>
python scripts/setup_cron.py resume --job-id <id>
```

## Pre-Run Checklist (before flipping `live_mode=true`)

Run through this every time the operator enables live writes — at
initial cutover and after any extended outage:

1. `op vault list` succeeds. The Service Account is reachable.
2. `op item get "PK Salesforce" --vault "PK Salesforce Skill" --otp`
   returns a fresh 6-digit code.
3. `python scripts/agent.py --command run --dry-run` succeeds end-to-
   end on one Lead and produces a clean `.docx`.
4. Operator and the human owner have reviewed at least five dry-run
   Notes and explicitly signed off on the format.
5. `config.json` has `inputs.live_mode = true`, `monthly_close_target_usd`
   matches the current target, and `google_drive_folder_id` +
   `nathan_share_email` are non-empty.
6. SerenBucks balance covers at least one full daily run plus a
   weekly run with margin.
7. Salesforce Lightning is reachable from the host (no VPN /
   firewall block).
8. No other operator is mid-edit on the All Sources PK Leads report
   or PK Lead Dashboard. The skill drives those artifacts and will
   stomp concurrent edits.

If any item fails, do not flip `live_mode=true`. Fail closed.

## Emergency Stop

If a bad Note format ships to production or the skill starts writing
into the wrong division, stop it immediately:

```bash
# 1. Pause the cron jobs so no new ticks fire.
python scripts/setup_cron.py list
python scripts/setup_cron.py pause --job-id <daily-job-id>
python scripts/setup_cron.py pause --job-id <weekly-job-id>

# 2. Flip the config gate off so even a manual run cannot write.
#    Edit config.json: set "inputs.live_mode": false.

# 3. Stop the local pull runner process.
#    Ctrl-C the foreground process, or `kill` it if backgrounded.
```

These three steps independently block writes. Any one is enough to
stop new Notes; the recommendation is all three so a tired operator
cannot accidentally undo the stop.

The skill does not auto-delete or auto-rollback Notes that have
already been written. If a bad Note batch shipped, the operator
manually cleans it up in Salesforce and the local pull runner stays
paused until the renderer is fixed and re-validated against
dry-runs.

## Privacy & Compliance

This skill handles customer-confidential CRM data. Read this before
operating it.

- **Credentials never leave the host.** The Service Account token,
  Salesforce login, and TOTP are read from 1Password at runtime,
  held in memory for the duration of one run, and discarded when
  the process exits. They are never written to logs, screenshots,
  or committed files.
- **No screenshots.** The skill takes Playwright screenshots only
  for selector debugging during development. The `.gitignore`
  blocks `*.png` to keep dev screenshots out of git. Never paste
  Salesforce screenshots into chat or share them outside the org —
  they always carry PII.
- **No bulk LinkedIn scraping.** The research module uses
  search-engine queries to find candidate LinkedIn URLs and
  surfaces the URL plus a match-confidence score. It does not
  enumerate connections, scrape profile contents in bulk, or
  bypass LinkedIn's session model.
- **Salesforce is the source of truth.** When LinkedIn and
  Salesforce disagree on a person's title or role, the Note
  surfaces the discrepancy as an observation. It does not silently
  overwrite the Salesforce record with the LinkedIn value.
- **SerenDB persistence.** The skill writes a durable
  `enriched_leads` row per enrichment so the same Lead is not
  re-researched on every run. Rows are kept indefinitely — the
  audit trail of what the skill told the human owner about each
  Lead is part of the deliverable. Never delete rows from the
  ledger; update in place if a Lead is re-enriched.
- **Division boundary.** PK is one division of four (PK / PL / MD
  / NW). The skill only acts on records flagged `PACKAGING = True`.
  A mis-routed enrichment that lands a Note on a non-PK Lead is a
  P0 defect, not a cosmetic bug.
- **Note content is human-reviewable.** Every Note carries the
  enrichment timestamp, the research sources used, and a short
  hypothesis section. If a downstream reader cannot reconstruct
  why the Note says what it says, the renderer has regressed —
  fail closed and fix the renderer instead of writing the Note.
- **Tax / legal interpretations are out of scope.** The skill
  surfaces information; it does not classify deals, render tax
  positions, or make compliance judgments. Those decisions stay
  with the human owner.

If any of these guarantees is unclear or violated by a code path,
treat it as a release-blocking bug. Privacy and division-boundary
defects are not "ship and patch later" — they are stop-the-line.

## Failure Modes

The companion `docs/failure_modes.md` (ships in phase 5) covers each
of the recurring operational failures and the recovery procedure for
each — Salesforce session expiry, Microsoft Authenticator drift,
Playwright selector rotation, Perplexity / Claude rate limits,
Google Drive sharing failures, SerenBucks depletion, etc. Until that
file lands, escalate non-trivial failures to the implementing
engineer; do not freelance recovery steps that touch live Salesforce
records.

## Disclaimer

This skill drives a real production Salesforce org as a real human
user. It can write Notes that other people read. Bugs in the
renderer or division boundary produce real-world cleanup work in a
customer-confidential system. Honor the dry-run gate. Honor the
`live_mode` + `--allow-live` double gate. Pause the cron at the
first sign of trouble. The skill is software tooling and not
financial, legal, or tax advice.

Taariq Lewis, SerenAI, Paloma, and Volume at https://serendb.com
Email: hello@serendb.com
