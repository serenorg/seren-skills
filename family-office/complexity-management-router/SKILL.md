---
name: complexity-management-router
display-name: "Family Office — Complexity Management Router"
description: "Pillar router for Complexity Management. Classifies a natural-language advisor ask into the matching leaf skill inside the Complexity Management pillar. Catalog rebuild tracked in issue #427."
---

# Family Office — Complexity Management Router

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active.

## When to Use

Invoke when the advisor asks a natural-language question about **Complexity Management**
without naming a specific leaf skill. Representative phrases:

- tax strategy
- tax planning memo
- CPA checklist
- tax package checklist
- external advisors
- manage outside advisors
- annual budget
- family budget
- cashflow forecast
- cash management

## Dispatch Table

This router maps advisor prompts to the matching leaf skill inside the
Complexity Management pillar. The table is embedded here (authoring source of truth)
and mirrored in `DISPATCH.yml` for programmatic consumers.

| Leaf slug | Artifact | Example triggers |
|---|---|---|
| `family-office-tax-strategy-memo` | Tax Strategy Memo | tax strategy; tax planning memo; year-end tax planning; tax posture |
| `family-office-cpa-tax-package-checklist` | CPA Tax Package Checklist | CPA checklist; tax package checklist; tax documents for CPA; CPA document list |
| `family-office-external-advisor-management-plan` | External Advisor Management Plan | external advisors; manage outside advisors; advisor roster; legal and tax management plan |
| `family-office-annual-budget-workbook` | Annual Budget Workbook | annual budget; family budget; create budget; budget workbook |
| `family-office-cashflow-forecast-worksheet` | Cashflow Forecast Worksheet | cashflow forecast; cash management; 12-month cash plan; rolling cashflow |
| `family-office-expedited-funding-access-plan` | Expedited Funding Access Plan | expedited funding; emergency liquidity; large cash access; SBLOC plan |
| `family-office-bookkeeping-bill-pay-setup-plan` | Bookkeeping & Bill Pay Setup Plan | bookkeeping setup; bill pay plan; family office accounting; internal controls |
| `family-office-document-management-plan` | Document Management Plan | document management; DMS plan; folder taxonomy; document retention |
| `family-office-consolidated-reporting-spec` | Consolidated Reporting Specification | consolidated reporting; reporting spec; family-office reporting; portfolio reporting requirements |
| `family-office-insurance-coverage-review-worksheet` | Insurance Coverage Review Worksheet | insurance review; coverage review; policy audit; insurance worksheet |
| `family-office-insurance-procurement-framework` | Insurance Procurement Framework | insurance procurement; buy insurance; insurance RFP; coverage procurement factors |
| `family-office-real-estate-acquisition-plan` | Real Estate Acquisition Plan | real estate acquisition; buy property plan; RE acquisition; property purchase plan |
| `family-office-art-acquisition-due-diligence` | Art Acquisition Due-Diligence Memo | art acquisition; art due diligence; buy painting; Warhol purchase; art DD |
| `family-office-leisure-asset-ownership-comparison` | Leisure Asset Ownership Comparison | jet vs NetJets; leisure asset comparison; fractional vs full ownership; yacht ownership model |
| `family-office-collectibles-acquisition-logistics` | Collectibles Acquisition & Logistics Plan | collectibles logistics; classic car transport; rare wine acquisition; Ferrari transport |
| `family-office-concierge-private-aviation-plan` | Private Aviation Coordination Plan | private aviation plan; charter aircraft; jet booking plan; flight coordination |
| `family-office-concierge-private-car-plan` | Private Car Coordination Plan | private car plan; driver coordination; chauffeur plan; car service roster |
| `family-office-concierge-travel-coordination-plan` | Travel Coordination Plan | travel coordination; trip plan; itinerary; family trip logistics |
| `family-office-concierge-personal-shopping-plan` | Personal Shopping Plan | personal shopping; shopping plan; gift shopping; wardrobe plan |
| `family-office-concierge-personal-protection-plan` | Personal Protection Plan | personal protection; executive protection; security plan; physical security |
| `family-office-password-management-setup-plan` | Password Management Setup Plan | password manager setup; 1Password plan; credential vault; family password setup |
| `family-office-virtual-mailbox-setup-plan` | Virtual Mailbox Setup Plan | virtual mailbox; mail scanning; mail forwarding plan; remote mail setup |

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
