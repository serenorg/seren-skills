---
name: wire-beneficiary-verification-gate
description: "Family office: Fraud and wire-verification control operator that screens outbound payment instructions against a known-good beneficiary register, halts new or changed banking details, creates verification tasks, and records a dry-run-first audit trail before any safe-to-pay mark."
---

# Family Office · Wire Beneficiary Verification Gate

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

This is a recurring operator skill, not a memo generator. Run it when a payment instruction, invoice, capital call, vendor bank-change notice, or queued outbound wire needs fraud screening against the family office's known-good beneficiary register.

## When to Use

- screen a wire beneficiary
- verify payment instructions before wire
- check vendor bank change against known good register
- run the family office wire verification gate
- review a new payee, changed ABA, changed account, changed beneficiary, or changed SWIFT instruction

## Operating Contract

The agent sits in front of outbound payment workflows. It compares each instruction against the persisted known-good register and records a branch-scoped audit event in SerenDB. Any new payee or changed banking fingerprint is a hard halt until a human completes an out-of-band callback using the known-good number already in the register.

The agent never releases a wire, never edits the known-good register from an inbound instruction alone, never treats the instruction's phone number as trusted, and never logs raw account numbers. Register writes and safe-to-pay marks require human-witnessed approval.

## Schema Guard

Before any read or write, resolve or create the SerenDB project `family-office-wire-beneficiary-verification-gate` and database `family_office_wire_beneficiary_verification_gate`. Verify or create these tables:

- `beneficiary_register`
- `payment_screen_events`
- `verification_tasks`
- `halt_ledger`
- `fraud_flags`

If schema provisioning fails, stop before provider calls. A missing schema is a setup blocker, not a reason to continue in memory.

## Seren Passwords

Use Seren Passwords for named secret references only. Config names the vault and item titles; it must not contain vault IDs, item IDs, raw credentials, or any Glide vault reference. Environment and cloud secret-store values may satisfy `SEREN_API_KEY`; provider credentials should be resolved from the named `Family Office Operations` vault after the operator grants access.

## Workflow Summary

1. `normalize_instruction` uses `transform.normalize_payment_instruction`
2. `resolve_seren_passwords_context` uses `connector.passwords.post`
3. `ensure_operator_database` uses `connector.storage.post`
4. `load_known_good_register` uses `connector.storage.post`
5. `scan_payment_sources` uses `connector.gmail.get`
6. `screen_instruction` uses `transform.screen_against_known_good_register`
7. `persist_audit_event` uses `connector.storage.post`
8. `create_verification_task` uses `connector.storage.post`
9. `send_dry_run_digest` uses `connector.outlook.post`
10. `summary` uses `transform.render_operator_summary`

## Dry-Run Behavior

Dry-run is the default. It screens the supplied instruction, emits the audit event and verification task payloads, and routes review output to `dry_run_to` only. The default dry-run recipient is `taariq@serendb.com`.

Run:

```bash
python3 scripts/agent.py --functional-test --config config.example.json
python3 scripts/agent.py --once --config config.example.json
```

## Live Gate

Live mode is blocked unless both conditions are true:

- CLI includes `--allow-live`
- `config.json` sets `live_mode: true` and `dry_run: false`

Even in live mode, a changed bank fingerprint produces a halt and a verification task. A safe-to-pay mark requires `allow_safe_to_pay: true` after out-of-band verification is complete.

## Cloud Deployment

Deploy as a seren-cloud cron or event-triggered worker with the default Python runtime. Suggested cron for polling payment sources is every 15 minutes in the office timezone, plus a daily digest run. Do not use Docker, a local pull runner, or customer-specific local paths.

## Functional Verification

For each PR, run the critical tests and the dry-run:

```bash
python3 -m pytest family-office/wire-beneficiary-verification-gate/tests -q
python3 family-office/wire-beneficiary-verification-gate/scripts/agent.py --functional-test --config family-office/wire-beneficiary-verification-gate/config.example.json
node scripts/build-index.mjs > /tmp/seren-skills-index.json
```

If the functional dry-run cannot return `all_green`, open a GitHub issue with label `bug`, assign it to `taariq`, fix through a new worktree and PR, then rerun this verification.

## Public Repo Rules

Do not commit credentials, account numbers, ABA/SWIFT values, raw beneficiary names from production, live vendor exports, generated bank packets, or real payment instructions. `config.example.json` uses synthetic fingerprints only.
