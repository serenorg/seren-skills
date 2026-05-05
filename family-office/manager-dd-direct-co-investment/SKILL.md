---
name: manager-dd-direct-co-investment
description: "Family office: Produce a due diligence memo for a direct or co-investment opportunity alongside a sponsor. Captures company, thesis, cap table, terms, and concentration risk."
---
# Family Office · Direct / Co-Investment DD Memo

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not
perform filesystem searches or tool-driven exploration to rediscover them; use
the guidance below directly.

## When to Use

Invoke when the advisor asks about:

- direct investment DD
- co-investment memo
- SPV memo
- direct co-invest

## Customer Pain (VOC)

Synthesized from family-office operator interviews (see `references/voc-evidence.md` for full provenance):

- **Co-invests must let the FO add real operating value** — *[4T0BpwFhRvo @ 09:07]*
- **FOs prefer co-investing alongside trusted partners, not solo** — *[hb8F0e9tW2Q @ 00:04]*
- **Co-investment syndication is the default early-stage posture** — *[hb8F0e9tW2Q @ 03:28]*

## Artifact Specification

This skill produces a single named deliverable:

- **Artifact:** `Direct / Co-Investment DD Memo`
- **Format at this iteration:** markdown (`artifact.md`) plus a structured
  `interview.json` capturing every advisor input. PDF / DOCX / XLSX companion
  renders and DMS / Snowflake push land in the execution-pipeline PR tracked
  under issue #427.

The artifact is written to a skill-local path, not a global directory:

```
<invocation-cwd>/artifacts/family-office/manager-dd-direct-co-investment/<YYYYMMDD-HHMMSS>/
  artifact.md
  interview.json
  manifest.json
```

## Interview Inputs

Minimum-viable interview — the skill asks only what is needed to personalize
the deliverable. Pre-fill rules against family memory land in the execution-
pipeline PR.

- `company_name` — Company name?
- `sponsor` — Lead sponsor?
- `round_type` — Round type (Series X / secondary / co-invest SPV)?
- `check_size` — Proposed check size?
- `valuation` — Valuation?
- `concentration_after` — Portfolio concentration after this investment (%)?
- `key_terms` — Key terms?

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
- `source` = `"manager-dd-direct-co-investment"`
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
