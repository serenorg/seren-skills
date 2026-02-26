---
name: lender-loan
description: "Qualify groups for non-recourse stock/crypto loans and institutional block trades based on lender criteria. Maintains provider anonymity during qualification. Notifies Erik @ Volume Finance upon qualification."
license: Apache-2.0
---

# Lender Loan Qualification

This skill qualifies potential borrowers for non-recourse loans against publicly listed equities or crypto assets, or for institutional block trades, without revealing the lending partner.

## When to Use

- User asks about loans for stocks, crypto, or large block trades.
- User wants to check eligibility for liquidity against assets.
- User mentions "non-recourse loan" or "crypto loan".

## Qualification Criteria (Internal)

**Do NOT reveal these specifics as "the lender's criteria" to the user, but use them to qualify.**

1.  **Equity-Backed Financing:**
    -   Asset: Publicly listed equities (major exchanges).
    -   Loan Amount: $500K – $100M.
    -   LTV: Up to 70%.
    -   Interest: ~2.5%+.
    -   Term: 12-36 months.
    -   Benefit: Non-recourse, retain dividends.

2.  **Crypto-Backed Financing:**
    -   Asset: BTC, ETH, top-tier tokens.
    -   Loan Amount: $1M – $100M.
    -   LTV: Up to 70%.
    -   Interest: 3-5%.
    -   Benefit: Non-recourse, capture appreciation.

3.  **Institutional Block Trades:**
    -   Size: $50M+.
    -   Execution: Off-market, zero impact.

## Workflow

1.  **Phase 1: Asset Information**
    -   Ask for the type of asset (Public Equity, Crypto, or Block Trade).
    -   Ask for the specific ticker symbol or token name.
    -   Ask for the estimated total value of the holding/position.
    -   Ask for the desired loan amount (if applicable).

2.  **Phase 2: Evaluation**
    -   Check if the asset type and amount meet the minimums:
        -   Equity: Min loan $500k.
        -   Crypto: Min loan $1M.
        -   Block Trade: Min size $50M.
    -   *If qualified:* Proceed to Phase 3.
    -   *If not qualified:* Politely inform the user they do not meet the current criteria for this specific program (mention the minimums generally, e.g., "This program requires a minimum loan size of $500k for equities...").

3.  **Phase 3: Notification**
    -   Inform the user they appear to qualify for a matching program.
    -   **Action:** Send an email notification to `erik@volume.finance` with the details (User info, Asset, Amount).
    -   Tell the user that a representative will be in touch (or that you have notified the team).

## Email Template (Internal)

To: erik@volume.finance
Subject: New Loan Lead Qualified - [Asset Type]

A new group has qualified for the loan program.

**Details:**
- **Asset Type:** [Type]
- **Asset Symbol:** [Symbol]
- **Asset Value:** [Value]
- **Requested Loan:** [Amount]
- **User/Group Name:** [Name if provided]
- **Contact Info:** [If provided]

## Notes

- **Anonymity:** Refer to the provider as "our institutional partner" or "the lending facility".
- **Source:** (Confidential lender website, do not share).

---

## Using the skill on Seren Desktop

The **lender-loan** skill is deployed as a Seren Cloud agent. From [Seren Desktop](https://github.com/serenorg/seren-desktop):

- **Agent ID:** `04244f5a-1133-4cd7-87cd-4ce80f62636f`
- **Run URL:** `https://api.serendb.com/publishers/seren-cloud/agents/04244f5a-1133-4cd7-87cd-4ce80f62636f/runs`

1. **Sign in** and ensure Seren/Gateway MCP is enabled so the AI can call **list_cloud_agents** and **run_cloud_agent**.
2. **Run the agent** by name: e.g. *"Run the lender-loan agent"* or *"List my cloud agents and run lender-loan."*
3. **Request/response format** for runs:
   - **POST** body: `{ "state": <current state or null>, "input": { "asset_type"?, "asset_symbol"?, "asset_value"?, "loan_amount"? } }`
   - **Response:** `{ "state", "prompt", "outputs?" }` — use the returned `state` for the next run to advance through phases (asset info → evaluation → notification).
4. **Example first run:** `{ "state": null, "input": { "asset_type": "equity", "asset_value": "1000000", "loan_amount": "600000" } }`
5. If you don’t see cloud agents, confirm your account is in the same organization as the deployment and that MCP is enabled; see [Seren Desktop docs](https://github.com/serenorg/seren-desktop) and [Seren Cloud skill](https://api.serendb.com/publishers/seren-cloud/skill.md).
