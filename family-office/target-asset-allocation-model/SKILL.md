---
name: target-asset-allocation-model
display-name: "Target Asset Allocation Model"
description: "Produce a target asset allocation model across public equities, fixed income, alternatives, real assets, and cash. Captures current allocation, target allocation, and drift bands."
---

# Target Asset Allocation Model

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not
perform filesystem searches or tool-driven exploration to rediscover them; use
the guidance below directly.

## When to Use

Invoke when the advisor asks about:

- asset allocation
- target allocation
- allocation model
- portfolio weights

## Artifact Specification

This skill produces a single named deliverable:

- **Artifact:** `Target Asset Allocation Model`
- **Format at this iteration:** markdown (`artifact.md`) plus a structured
  `interview.json` capturing every advisor input. PDF / DOCX / XLSX companion
  renders and DMS / Snowflake push land in the execution-pipeline PR tracked
  under issue #427.

The artifact is written to a skill-local path, not a global directory:

```
<invocation-cwd>/artifacts/family-office/target-asset-allocation-model/<YYYYMMDD-HHMMSS>/
  artifact.md
  interview.json
  manifest.json
```

## Interview Inputs

Minimum-viable interview — the skill asks only what is needed to personalize
the deliverable. Pre-fill rules against family memory land in the execution-
pipeline PR.

- `current_equities_pct` — Current public equity allocation (%)?
- `current_fi_pct` — Current fixed income allocation (%)?
- `current_alts_pct` — Current alternatives allocation (%)?
- `target_equities_pct` — Target public equity allocation (%)?
- `target_fi_pct` — Target fixed income allocation (%)?
- `target_alts_pct` — Target alternatives allocation (%)?
- `drift_band_pct` — Drift band (± %) before rebalance?

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
- `source` = `"target-asset-allocation-model"`
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
