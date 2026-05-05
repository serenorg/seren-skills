---
name: insurance-coverage-review-worksheet
description: "Family office: Produce a worksheet reviewing all insurance coverage — health, life, property, auto/marine, liability, umbrella, casualty. Captures policies in force, gaps, and renewal dates."
---
# Family Office · Insurance Coverage Review Worksheet

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not
perform filesystem searches or tool-driven exploration to rediscover them; use
the guidance below directly.

## When to Use

Invoke when the advisor asks about:

- insurance review
- coverage review
- policy audit
- insurance worksheet

## Customer Pain (VOC)

Synthesized from family-office operator interviews (see `references/voc-evidence.md` for full provenance):

- **Load policy, ChatGPT extracts expiration and updates the database** — *[dLoQJfqGgag @ 26:49]*
- **Simple question "when does my policy expire?" still goes unanswered** — *[dLoQJfqGgag @ 26:43]*
- **Out-of-state coverage often gets refused at point of care** — *[t1gU3F9AE-E @ 16:17]*
- **Insurance review must coordinate with tax, bill-pay, and wealth advisors** — *[HV86G3RCPV0 @ 04:15]*

## Artifact Specification

This skill produces a single named deliverable:

- **Artifact:** `Insurance Coverage Review Worksheet`
- **Format at this iteration:** markdown (`artifact.md`) plus a structured
  `interview.json` capturing every advisor input. PDF / DOCX / XLSX companion
  renders and DMS / Snowflake push land in the execution-pipeline PR tracked
  under issue #427.

The artifact is written to a skill-local path, not a global directory:

```
<invocation-cwd>/artifacts/family-office/insurance-coverage-review-worksheet/<YYYYMMDD-HHMMSS>/
  artifact.md
  interview.json
  manifest.json
```

## Interview Inputs

Minimum-viable interview — the skill asks only what is needed to personalize
the deliverable. Pre-fill rules against family memory land in the execution-
pipeline PR.

- `coverage_types_in_scope` — Coverage types in scope (comma-separated)?
- `carriers_in_force` — Carriers in force?
- `known_gaps` — Known gaps in coverage?
- `renewal_dates_near_term` — Near-term renewal dates?
- `broker_relationship` — Primary insurance broker?

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
- `source` = `"insurance-coverage-review-worksheet"`
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
