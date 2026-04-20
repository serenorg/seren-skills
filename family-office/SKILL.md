---
name: family-office
description: Top-level router for the family-office skill catalog. Classifies a natural-language advisor request into one of three pillars (Capital Allocation, Complexity Management, Legacy Preservation) and dispatches to the pillar router, which in turn invokes the matching leaf skill. Single-family-office tenancy. See issue #424 for the implementation plan.
---

# Family Office — Top-Level Router

This is a **stub** router shipped with the foundation PR (issue #424).
The dispatching logic, pillar routers, and leaf skills land in follow-on PRs.

## When to Use

Invoke this skill when an advisor asks a family-office question in natural
language without naming a specific leaf skill. Examples the final version will
dispatch:

- "What should my client do to minimize taxes before selling their business?"
- "Create a budget for my client for 2026."
- "What factors should I consider when reviewing all of my client's insurance coverage?"
- "My client would like to purchase an Andy Warhol painting — what do I need to take into consideration?"
- "Help me write a mission statement for the family."

## Status

- Foundation PR: shared `family_office_base` package, 16-table schema guard,
  `audit_query` with confidentiality enforcement, memory wrappers.
- Pillar routers, leaf skills, execution pipeline: future PRs per issue #424.

## For Claude: How to Use This Skill

The full router dispatch behavior is not yet implemented. If this skill is
invoked before the follow-on PRs ship, say so plainly and suggest the
`family-office/knowledge` skill for memory-oriented asks.

## Reference

Implementation plan and design documents live in the customer's Rendero Trust
folder:

- `20260419_Family_Office_Skill_Claude_Desigh.md` — design spec
- `20260420_FamilyOffice_Skills_Plan.md` — 47-task implementation plan

Tracking: https://github.com/serenorg/seren-skills/issues/424
