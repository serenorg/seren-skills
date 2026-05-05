---
name: bookkeeping-bill-pay-setup-plan
description: "Family office: Produce a setup plan for family-office bookkeeping and bill pay operations — software, approvers, reconciliation, internal controls."
---
# Family Office · Bookkeeping & Bill Pay Setup Plan

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not
perform filesystem searches or tool-driven exploration to rediscover them; use
the guidance below directly.

## When to Use

Invoke when the advisor asks about:

- bookkeeping setup
- bill pay plan
- family office accounting
- internal controls

## Customer Pain (VOC)

Synthesized from family-office operator interviews (see `references/voc-evidence.md` for full provenance):

- **Lighter-footprint family offices want to retire in-house bill-pay clerks** — *[HV86G3RCPV0 @ 13:05]*
- **Principals unsure whether existing op-co bookkeepers can cover FO accounting** — *[HV86G3RCPV0 @ 16:43]*
- **AP-to-GL touch points need deliberate automation, not ad-hoc glue** — *[_URp9ryeUlQ @ 08:08]*
- **GL is foundational but the surrounding tech stack must be built with intent** — *[_URp9ryeUlQ @ 08:30]*
- **Bookkeeping sits inside a wider risk and reporting stack, not standalone** — *[f4vwiAXPj3s @ 08:52]*

## Artifact Specification

This skill produces a single named deliverable:

- **Artifact:** `Bookkeeping & Bill Pay Setup Plan`
- **Format at this iteration:** markdown (`artifact.md`) plus a structured
  `interview.json` capturing every advisor input. PDF / DOCX / XLSX companion
  renders and DMS / Snowflake push land in the execution-pipeline PR tracked
  under issue #427.

The artifact is written to a skill-local path, not a global directory:

```
<invocation-cwd>/artifacts/family-office/bookkeeping-bill-pay-setup-plan/<YYYYMMDD-HHMMSS>/
  artifact.md
  interview.json
  manifest.json
```

## Interview Inputs

Minimum-viable interview — the skill asks only what is needed to personalize
the deliverable. Pre-fill rules against family memory land in the execution-
pipeline PR.

- `accounting_platform` — Accounting platform (Sage Intacct / QuickBooks / AtlasFive)?
- `bill_pay_platform` — Bill pay platform?
- `approval_thresholds` — Approval thresholds (principal / COO / staff)?
- `reconciliation_cadence` — Reconciliation cadence?
- `audit_trail_retention` — Audit trail retention period?

## Workflow

1. Run `python scripts/agent.py --config config.json` (or invoke via Claude
   Code with a config blob).
2. The agent validates config, runs the interview (TTY or fixture-driven),
   and produces the artifact under the canonical local path.
3. The agent writes `manifest.json` with artifact metadata (name, version,
   content hash, pillar, skill_name, created_at).
4. The agent optionally writes memory entries to the knowledge skill's
   `memory_objects` table via psycopg if `config.memory_dsn` is provided.
   Without a DSN, memory writes are skipped cleanly.

## Memory Conventions

Memories written by this skill are tagged with:

- `subject` = the artifact name
- `source` = `"bookkeeping-bill-pay-setup-plan"`
- `memory_type` ∈ {`decision`, `assumption`, `commitment`, `open_question`}

## Security & Confidentiality

- Never log interview answers or artifact contents at INFO level.
- Never include SSN, EIN, account numbers, or full financial amounts in
  log lines. If a WHERE-clause field carries such data, log only a sha256
  hash.
- The artifact directory is local-only at this iteration. DMS push (with
  confidentiality-label routing) is handled by the execution-pipeline PR.

## Reference

- Design spec: `20260419_Family_Office_Skill_Claude_Desigh.md`
- Implementation plan: `20260420_FamilyOffice_Skills_Plan.md`
- Catalog rebuild tracking: https://github.com/serenorg/seren-skills/issues/427
