# Operator Checkpoint Runbook

This document is the operator-and-engineer pair runbook for the
**Salesforce sandbox checkpoint** that lands the live UI drivers for
Phase 3 (schema + reporting provisioning) and Phase 4 (live Note write).

Until this checkpoint runs, `--allow-live` paths fail closed with
`NotImplementedError` from the stubs in `scripts/sf/`. See issue
[#563](https://github.com/serenorg/seren-skills/issues/563) and the
`Status by Phase` section of `SKILL.md`.

## Why this exists

The pure logic for Phase 3 and Phase 4 — gates, idempotency checks,
recency checks, renderers, the dual `--allow-live` × `live_mode=true`
gate — is real and unit-tested. The **Playwright code that drives
Salesforce Lightning's DOM** is not testable headlessly: Lightning's
selectors rotate on schedule, the Object Manager / Report Builder /
Dashboard Builder / Note form are operator-permission-gated, and the
authenticated session can only be observed by a human reviewing the
browser.

This runbook moves those stubs from `NotImplementedError` to live code
in a single supervised session.

## Prerequisites

1. A **Salesforce sandbox org** (not production) with the same Lead schema as production. Sandbox refreshes are fine; the goal is selectors, not data.
2. A 1Password vault entry for the sandbox login (username, password, TOTP). The vault and item names match the production `OP_VAULT` / `OP_ITEM` so the same code path drives both.
3. The same Microsoft SSO + TOTP flow that production uses, pointing at the sandbox.
4. The pk-lead-intelligence skill checked out and `.venv` provisioned: `pip install -r requirements.txt && playwright install chromium`.
5. `.env` populated with the sandbox `SEREN_API_KEY` and `OP_SERVICE_ACCOUNT_TOKEN`.
6. `config.json` with `inputs.salesforce_org_url` pointing at the sandbox and `inputs.live_mode: true`.
7. A scratch PK Lead in the sandbox with `PACKAGING__c = true`, used as the test target.
8. An engineer at the keyboard with edit access to `scripts/sf/`.

## Session protocol

The checkpoint is one supervised session, headful, ~3–4 hours. The
operator watches the browser and confirms each artifact landed; the
engineer fills in selectors as they're observed.

### Phase 3 — schema + reporting provisioning

Goal: replace 8 stubs in `scripts/sf/provision_fields.py`,
`scripts/sf/build_all_sources_leads_report.py`,
`scripts/sf/build_pk_lead_dashboard.py`, and
`scripts/sf/build_pk_opp_artifacts.py` with live Lightning DOM driving.

1. Start headful: `python scripts/agent.py --command provision --dry-run`. Confirm the SSO + storage-state path lands on the sandbox home page.
2. Open Object Manager → Lead → Fields. For each Phase 3 field spec, capture the new-field selectors. Fill `_drive_new_field`. Re-run; confirm idempotency (a second run is a no-op).
3. Open Report Builder → All Sources PK Leads (or create it). Capture title-lookup + create selectors. Fill `_find_report_url_by_title` and `_drive_new_report`. Re-run; confirm `created=False` on the second pass.
4. Open Dashboard Builder → PK Lead Dashboard. Capture lookup + create selectors. Fill `_find_dashboard_url_by_title` and `_drive_new_dashboard` in `build_pk_lead_dashboard.py`. Re-run; confirm idempotent.
5. Repeat (4) for `build_pk_opp_artifacts.py`. Same selectors usually transfer; copy and adjust.
6. Run `pytest -m sandbox tests/sandbox/test_phase3_provision.py` (added at checkpoint time). All assertions pass.

### Phase 4 — live Note write

Goal: replace 3 stubs in `scripts/sf/write_note.py` with live Lightning DOM driving on the Lead's Related tab.

1. Open the scratch PK Lead. Capture the Related tab → Notes & Attachments → New Note flow. Fill `_drive_new_note_form`.
2. Capture how `Last_Enrichment_At__c` is read from the Lead detail panel. Fill `_read_last_enrichment_at`. Confirm the 24h recency gate skips a fresh Lead.
3. Capture how `Last_Enrichment_At__c` is updated after a write. Fill `_update_last_enrichment_at`. Confirm write-then-stamp ordering.
4. Run `pytest -m sandbox tests/sandbox/test_phase4_note_write.py` (added at checkpoint time). All assertions pass.
5. Drive one full live cycle: `python scripts/agent.py --command run --allow-live` against the scratch Lead. Operator confirms the Note rendered correctly in the Related tab. Engineer captures the run output for the merge PR.

## Deliverables out of the session

1. Updated `scripts/sf/*.py` files with real selectors replacing every `# pragma: no cover` `raise NotImplementedError`.
2. New `tests/sandbox/test_phase3_provision.py` and `tests/sandbox/test_phase4_note_write.py`, marked `@pytest.mark.sandbox`, that exercise the real Lightning DOM.
3. A merge PR titled `feat(pk-lead-intelligence): land Phase 3/4 live drivers (operator checkpoint <YYYY-MM-DD>)`. Include the session recording or selector transcript as a comment.
4. SKILL.md "Status by Phase" table updated: Phase 3 and Phase 4 flip from ⚠ to ✅.
5. `test_label_consistency.py` continues to pass (no stubs remain in `scripts/sf/`, so the test enters its vacuous-pass branch).

## What is explicitly **out** of scope for this checkpoint

- The `enriched_leads` ledger that feeds `lead_summaries` in the weekly doc. That is Phase 5 scope — a SerenDB table or JSON state directory. Until it exists, the weekly doc renders the empty-week template regardless of activity.
- The seren-cron jobs, the local-pull runner, and the `/pk-status` slash command. All Phase 5.
- Production-org work. This session is sandbox only. The first production run happens on a later, separately-scheduled cutover after at least one calendar day of green sandbox runs.

## Safety

The session is **sandbox only**. Do not point `inputs.salesforce_org_url` at production during the checkpoint. The pre-run checklist in `SKILL.md` ("Pre-Run Checklist") is the gate for the production cutover, not for this session.
