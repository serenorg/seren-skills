---
name: grant-intake
description: "Five-phase grant consultant intake for grant readiness. Use when a user needs structured intake for org name, mission, programs, sector selection (workforce, health, green economy, education, housing, arts, agriculture), logic models per program, sector-specific questions, and a Grant Readiness Summary plus Organizational Profile export."
license: Apache-2.0
---

# Grant Intake

Structured five-phase grant consultant intake to produce a Grant Readiness Summary and Organizational Profile.

## When to Use

- User wants grant readiness or funder alignment
- User needs to capture org mission, programs, and logic models (inputs, activities, outputs, outcomes)
- User is preparing for grant applications and needs sector-specific metrics and a summary document

## Workflow Summary

1. **Phase 1** — Org name, mission, key programs
2. **Phase 2** — Sector selection (Workforce Development, Health/Human Services, Green Economy, Education/Youth, Housing, Arts/Culture, Agriculture, or custom)
3. **Phase 3** — Logic model per program (inputs, activities, outputs, outcomes)
4. **Phase 4** — Sector-specific thematic questions with optional hints
5. **Phase 5** — Review, confirm, and export Grant Readiness Summary + intake state

## How to Run

- **Seren Cloud agent:** If the grant-intake cloud agent is available (e.g. via Seren Gateway or MCP), invoke it with a JSON body: `{ "state": null, "input": { "org_name": "...", "mission": "...", "key_programs": ["..."] } }`. Response is `{ "state", "prompt", "outputs?" }`; pass `state` back on each step to advance phases.
- **Docs and deploy:** See [grant-intake deploy and invoke docs](https://github.com/serendb/seren-sql-interface-402mcp-server/blob/main/docs/seren/README.md) for endpoint URL, agent ID, and scripts.

## Sectors

Predefined sectors: Workforce Development, Health/Human Services, Green Economy, Education/Youth, Housing, Arts/Culture, Agriculture. Custom sectors are supported.
