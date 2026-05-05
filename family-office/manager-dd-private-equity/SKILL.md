---
name: manager-dd-private-equity
description: "Family office: Produce a due diligence memo for a prospective private equity fund. Captures fund vintage, track record, sector focus, fee terms, and J-curve expectations."
---
# Family Office · Private Equity Manager DD Memo

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not
perform filesystem searches or tool-driven exploration to rediscover them; use
the guidance below directly.

## When to Use

Invoke when the advisor asks about:

- private equity manager DD
- PE fund due diligence
- buyout fund memo
- PE allocation

## Customer Pain (VOC)

Synthesized from family-office operator interviews (see `references/voc-evidence.md` for full provenance):

- **DD has to be intense, not box-checking** — *[4T0BpwFhRvo @ 06:51]*
- **Insourcing DD only pencils above the billion-dollar mark** — *[HV86G3RCPV0 @ 22:39]*
- **Trust takes time — vetting partners can't be rushed** — *[HV86G3RCPV0 @ 25:17]*
- **Alts data lives in silos that won't assimilate cleanly** — *[45pe5yImE9s @ 01:03]*
- **Talent retention is a chronic FO bottleneck** — *[_URp9ryeUlQ @ 04:53]*

## Artifact Specification

This skill produces a single named deliverable:

- **Artifact:** `Private Equity Manager DD Memo`
- **Format at this iteration:** markdown (`artifact.md`) plus a structured
  `interview.json` capturing every advisor input. PDF / DOCX / XLSX companion
  renders and DMS / Snowflake push land in the execution-pipeline PR tracked
  under issue #427.

The artifact is written to a skill-local path, not a global directory:

```
<invocation-cwd>/artifacts/family-office/manager-dd-private-equity/<YYYYMMDD-HHMMSS>/
  artifact.md
  interview.json
  manifest.json
```

## Interview Inputs

Minimum-viable interview — the skill asks only what is needed to personalize
the deliverable. Pre-fill rules against family memory land in the execution-
pipeline PR.

- `manager_name` — GP / manager name?
- `fund_name` — Fund name / vintage?
- `sector_focus` — Sector focus?
- `fund_size` — Target fund size?
- `prior_fund_tvpi` — Prior fund TVPI?
- `prior_fund_dpi` — Prior fund DPI?
- `fee_terms` — Fee terms (mgmt / carry / hurdle)?
- `minimum_commit` — Minimum commitment size?

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
- `source` = `"manager-dd-private-equity"`
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
