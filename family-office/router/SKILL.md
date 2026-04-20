---
name: router
display-name: "Family Office — Top-Level Router"
description: "Top-level natural-language router for the Seren family-office catalog. Classifies an advisor ask into one of three pillars (Capital Allocation, Complexity Management, Legacy Preservation) and delegates to the pillar router, which in turn invokes the matching leaf. Single-family-office tenancy. Catalog rebuild tracked in issue #427."
---

# Family Office — Top-Level Router

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active.

## When to Use

Invoke when the advisor asks any family-office question in natural language
without naming a specific skill or pillar. Representative phrases:

- "What should my client do to minimize taxes before selling their business?"
- "Create a budget for my client for 2026."
- "My client would like to purchase an Andy Warhol painting — what do I need
  to take into consideration?"
- "Help me write a mission statement for a family."
- "Plan international travel to Egypt focused on personal security."

## Three-Pillar Framing

Every family-office ask fits into one of three pillars:

- **Capital Allocation** — portfolio strategy, asset allocation, risk, manager
  due diligence, exits, new-business evaluation, alternatives, ESG mandates.
- **Complexity Management** — tax, budgeting, cashflow, insurance, real
  estate / art / leisure-asset / collectibles acquisition, concierge services,
  credentials, mail.
- **Legacy Preservation** — estate planning, trusts, wills, healthcare
  directives, foundations, philanthropy, family governance, next-generation
  education, risk.

## Dispatch

1. Classify the ask into one of the three pillars using the pillar
   descriptions above.
2. Delegate to the matching pillar router:
   - `family-office-capital-allocation-router`
   - `family-office-complexity-management-router`
   - `family-office-legacy-preservation-router`
3. If the ask spans pillars, ask a single clarifying question before
   delegating.
4. If no pillar claims confidence, respond plainly that no leaf currently
   fits and suggest logging the ask as an open question in the knowledge
   skill.

## Demo script (for Rendero Trust)

Advisor: *"What should my client do to minimize taxes before selling their
business?"*
→ Top router classifies as **Capital Allocation**.
→ Delegates to `family-office-capital-allocation-router`.
→ That router matches trigger `"sell business"` → invokes
`family-office-business-exit-strategy`.
→ The leaf runs an interview, renders the Exit Tax Minimization Plan, and
writes artifact.md + interview.json + manifest.json under
`artifacts/family-office/business-exit-strategy/<timestamp>/`.

PDF / DOCX / XLSX renders, DMS push (SharePoint + Egnyte), Snowflake ingest,
and approval-gated execution actions land in the execution-pipeline PR
tracked under issue #427.

## Non-goals for this iteration

The top-level router does not programmatically invoke Python agents; the
Claude Code harness does that when the router names the right skill. This
skill's job is classification, not orchestration.

## Reference

- Design spec: `20260419_Family_Office_Skill_Claude_Desigh.md`
- Implementation plan: `20260420_FamilyOffice_Skills_Plan.md`
- Catalog rebuild tracking: https://github.com/serenorg/seren-skills/issues/427
