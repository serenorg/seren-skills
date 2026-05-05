---
name: portfolio-risk-register
description: "Family office: Produce a portfolio risk register. Captures concentration risks, liquidity risks, counterparty risks, tax-lot risks, and mitigations."
---
# Family Office · Portfolio Risk Register

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not
perform filesystem searches or tool-driven exploration to rediscover them; use
the guidance below directly.

## When to Use

Invoke when the advisor asks about:

- portfolio risk
- risk register
- risk management memo
- portfolio risks

## Customer Pain (VOC)

Synthesized from family-office operator interviews (see `references/voc-evidence.md` for full provenance):

- **Global trade war flagged as the year's top portfolio risk** — *[qldW5BOd5TQ @ 00:53]*
- **Five-year horizon: geopolitics, recession, debt-crisis dominate the register** — *[qldW5BOd5TQ @ 05:44]*
- **Quality tilt is the defense against permanent-loss drawdowns** — *[qldW5BOd5TQ @ 15:13]*
- **Founders need illiquidity premium or shouldn't be in private markets** — *[4T0BpwFhRvo @ 06:24]*
- **Crypto volatility can't be engineered or analyst-diligenced like a stock** — *[4T0BpwFhRvo @ 17:40]*

## Artifact Specification

This skill produces a single named deliverable:

- **Artifact:** `Portfolio Risk Register`
- **Format at this iteration:** markdown (`artifact.md`) plus a structured
  `interview.json` capturing every advisor input. PDF / DOCX / XLSX companion
  renders and DMS / Snowflake push land in the execution-pipeline PR tracked
  under issue #427.

The artifact is written to a skill-local path, not a global directory:

```
<invocation-cwd>/artifacts/family-office/portfolio-risk-register/<YYYYMMDD-HHMMSS>/
  artifact.md
  interview.json
  manifest.json
```

## Interview Inputs

Minimum-viable interview — the skill asks only what is needed to personalize
the deliverable. Pre-fill rules against family memory land in the execution-
pipeline PR.

- `concentration_exposures` — Top 3 concentration exposures and sizes?
- `illiquid_pct` — Percentage of portfolio in illiquid positions?
- `key_counterparties` — Key counterparties / custodians?
- `material_tax_lots` — Material low-basis tax lots that constrain rebalancing?
- `mitigations_in_place` — Mitigations already in place?
- `open_risks` — Open unresolved risks?

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
- `source` = `"portfolio-risk-register"`
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
