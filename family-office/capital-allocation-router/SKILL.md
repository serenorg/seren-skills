---
name: capital-allocation-router
display-name: "Family Office — Capital Allocation Router"
description: "Pillar router for Capital Allocation. Classifies a natural-language advisor ask into the matching leaf skill inside the Capital Allocation pillar. Catalog rebuild tracked in issue #427."
---

# Family Office — Capital Allocation Router

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active.

## When to Use

Invoke when the advisor asks a natural-language question about **Capital Allocation**
without naming a specific leaf skill. Representative phrases:

- long-term portfolio strategy
- investment policy statement
- asset allocation
- target allocation
- portfolio risk
- risk register
- business exit
- sell business
- new business diligence
- acquire business

## Dispatch Table

This router maps advisor prompts to the matching leaf skill inside the
Capital Allocation pillar. The table is embedded here (authoring source of truth)
and mirrored in `DISPATCH.yml` for programmatic consumers.

| Leaf slug | Artifact | Example triggers |
|---|---|---|
| `family-office-long-term-portfolio-strategy-plan` | Long-Term Portfolio Strategy Plan | long-term portfolio strategy; investment policy statement; portfolio plan; long horizon allocation |
| `family-office-target-asset-allocation-model` | Target Asset Allocation Model | asset allocation; target allocation; allocation model; portfolio weights |
| `family-office-portfolio-risk-register` | Portfolio Risk Register | portfolio risk; risk register; risk management memo; portfolio risks |
| `family-office-business-exit-strategy` | Exit Tax Minimization Plan | business exit; sell business; minimize taxes before selling; exit planning; operating business sale |
| `family-office-new-business-diligence-memo` | New Business Diligence Memo | new business diligence; acquire business; evaluate new business; business investment memo |
| `family-office-investment-operations-review-checklist` | Investment Operations Review Checklist | investment operations review; ops review; custody check; reconciliation review |
| `family-office-client-sourced-deal-review-memo` | Client-Sourced Deal Review Memo | client sourced deal; principal brought a deal; friend referral investment; review this deal |
| `family-office-manager-dd-hedge-fund` | Hedge Fund Manager DD Memo | hedge fund manager DD; hedge fund due diligence; HF allocation memo; evaluate hedge fund |
| `family-office-manager-dd-private-equity` | Private Equity Manager DD Memo | private equity manager DD; PE fund due diligence; buyout fund memo; PE allocation |
| `family-office-manager-dd-private-debt` | Private Debt Manager DD Memo | private debt DD; private credit manager; direct lending fund; private debt allocation |
| `family-office-manager-dd-venture-capital` | Venture Capital Manager DD Memo | venture capital manager DD; VC fund due diligence; seed fund memo; VC allocation |
| `family-office-manager-dd-real-assets` | Real Assets Manager DD Memo | real assets manager DD; infrastructure fund; natural resources fund; commodities allocation |
| `family-office-manager-dd-real-estate` | Real Estate Manager DD Memo | real estate manager DD; RE fund due diligence; property fund memo; real estate allocation |
| `family-office-manager-dd-direct-co-investment` | Direct / Co-Investment DD Memo | direct investment DD; co-investment memo; SPV memo; direct co-invest |
| `family-office-manager-dd-esg-impact` | ESG / Impact Manager DD Memo | ESG manager DD; impact fund due diligence; impact investing memo; ESG allocation |
| `family-office-esg-impact-investing-mandate` | ESG / Impact Investing Mandate | ESG mandate; impact mandate; impact investing policy; ESG investment policy |

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
