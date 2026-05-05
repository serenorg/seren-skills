---
name: cashflow-forecast-worksheet
display-name: "Family Office · Cashflow Forecast Worksheet"
description: "Family office: Produce a 12-month rolling cashflow forecast. Captures inflows, outflows, timing, and cushion requirements."
tags: [family-office, pillar:complexity-management]
---

# Cashflow Forecast Worksheet

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not
perform filesystem searches or tool-driven exploration to rediscover them; use
the guidance below directly.

## When to Use

Invoke when the advisor asks about:

- cashflow forecast
- cash management
- 12-month cash plan
- rolling cashflow

## Customer Pain (VOC)

Synthesized from family-office operator interviews (see `references/voc-evidence.md` for full provenance):

- **Founders facing a 12-18 month liquidity event need pre/during/post cash planning** — *[4T0BpwFhRvo @ 00:30]*
- **Pre-liquidity-event founders need a different cash posture than post-liquidity wealth** — *[4T0BpwFhRvo @ 10:27]*
- **Investment universes for waiting-on-liquidity founders differ from typical wealthy clients** — *[4T0BpwFhRvo @ 10:45]*
- **Estate teams often miss the principal's actual short-term liquidity needs** — *[HV86G3RCPV0 @ 20:24]*
- **Operators want better liquidity forecasting across many subscription agreements** — *[dLoQJfqGgag @ 29:14]*

## Artifact Specification

This skill produces a single named deliverable:

- **Artifact:** `Cashflow Forecast Worksheet`
- **Format at this iteration:** markdown (`artifact.md`) plus a structured
  `interview.json` capturing every advisor input. PDF / DOCX / XLSX companion
  renders and DMS / Snowflake push land in the execution-pipeline PR tracked
  under issue #427.

The artifact is written to a skill-local path, not a global directory:

```
<invocation-cwd>/artifacts/family-office/cashflow-forecast-worksheet/<YYYYMMDD-HHMMSS>/
  artifact.md
  interview.json
  manifest.json
```

## Interview Inputs

Minimum-viable interview — the skill asks only what is needed to personalize
the deliverable. Pre-fill rules against family memory land in the execution-
pipeline PR.

- `forecast_start_month` — Forecast start month (YYYY-MM)?
- `recurring_inflows` — Recurring inflows (source, amount, cadence)?
- `recurring_outflows` — Recurring outflows (category, amount, cadence)?
- `lumpy_known_items` — Known lumpy inflows/outflows and timing?
- `minimum_cushion` — Minimum liquidity cushion?

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
- `source` = `"cashflow-forecast-worksheet"`
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
