---
name: legacy-preservation-router
display-name: "Family Office · Legacy Preservation Router"
description: "Family office: Pillar router for Legacy Preservation. Classifies a natural-language advisor ask into the matching leaf skill inside the Legacy Preservation pillar. Catalog rebuild tracked in issue #427."
tags: [family-office, pillar:legacy-preservation, type:router]
---

# Family Office — Legacy Preservation Router

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active.

## When to Use

Invoke when the advisor asks a natural-language question about **Legacy Preservation**
without naming a specific leaf skill. Representative phrases:

- estate plan summary
- estate planning memo
- trust selection
- which trust
- will drafting
- update will
- healthcare proxy
- living will
- private trust company
- PTC formation

## Dispatch Table

This router maps advisor prompts to the matching leaf skill inside the
Legacy Preservation pillar. The table is embedded here (authoring source of truth)
and mirrored in `DISPATCH.yml` for programmatic consumers.

| Leaf slug | Artifact | Example triggers |
|---|---|---|
| `family-office-estate-plan-summary-memo` | Estate Plan Summary Memo | estate plan summary; estate planning memo; estate overview; estate structure |
| `family-office-trust-selection-memo` | Trust Selection Memo | trust selection; which trust; compare trust types; trust recommendation |
| `family-office-will-drafting-checklist` | Will Drafting Checklist | will drafting; update will; will checklist; testator checklist |
| `family-office-healthcare-proxy-living-will-checklist` | Healthcare Proxy & Living Will Checklist | healthcare proxy; living will; advance directive; medical power of attorney |
| `family-office-private-trust-company-formation-plan` | Private Trust Company Formation Plan | private trust company; PTC formation; family PTC plan; PTC jurisdiction |
| `family-office-trust-situs-selection-memo` | Trust Situs Selection Memo | trust situs; situs selection; jurisdiction comparison; where to form trust |
| `family-office-family-philanthropy-strategic-plan` | Family Philanthropy Strategic Plan | philanthropy plan; family giving strategy; charitable plan; philanthropic budget |
| `family-office-family-mission-statement` | Family Mission Statement | family mission; write mission statement; family values statement; family purpose |
| `family-office-family-foundation-formation-plan` | Family Foundation Formation Plan | family foundation; foundation formation; 501c3 plan; private foundation |
| `family-office-charitable-trust-selection-memo` | Charitable Trust Selection Memo | charitable trust; CRUT CLT comparison; charitable remainder trust; charitable lead trust |
| `family-office-family-governance-charter` | Family Governance Charter | family governance; governance charter; family council charter; family rules |
| `family-office-family-board-development-plan` | Family Board Development Plan | board development; family board plan; board skills matrix; board recruitment |
| `family-office-succession-planning-memo` | Succession Planning Memo | succession planning; successor plan; leadership transition; ownership succession |
| `family-office-family-meeting-agenda-minutes-template` | Family Meeting Agenda & Minutes Template | family meeting agenda; meeting minutes template; family council agenda; family meeting prep |
| `family-office-community-engagement-plan` | Community Engagement Plan | community engagement; civic involvement plan; nonprofit board service; public engagement plan |
| `family-office-next-generation-education-curriculum` | Next-Generation Education Curriculum | next generation curriculum; kids curriculum; family education plan; rising generation education |
| `family-office-family-risk-management-plan` | Family Risk Management Plan | family risk management; cyber risk plan; travel security plan; Egypt travel security; family security |

## Workflow

1. The router reads the advisor's prompt.
2. It matches trigger phrases to the leaf skill whose artifact best fits.
3. It confirms the match with the advisor if confidence is borderline
   ("Nearest skill is X — run it?").
4. If no leaf matches, it returns a clear "not built in this iteration"
   message and suggests logging the ask as an open question.

## Non-goals for this iteration

This router does not programmatically invoke the leaf skill's Python agent.
Cross-skill invocation is handled by the Claude Code harness: the router's
job is to name the right leaf so the harness invokes it. The programmatic
orchestration pipeline ships under the execution-pipeline PR tracked in
issue #427.

## Composition

If the advisor's ask implies more than one leaf artifact (example: "exit the
business AND plan the philanthropic vehicle that catches the proceeds"), the
router proposes a two-leaf sequence with a shared interview that minimizes
duplicate questions.

## Reference

- Design spec: `20260419_Family_Office_Skill_Claude_Desigh.md`
- Catalog rebuild tracking: https://github.com/serenorg/seren-skills/issues/427
