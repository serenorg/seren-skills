# Lender Loan Qualification Skill

This skill qualifies potential borrowers for non-recourse stock/crypto loans and institutional block trades based on lender's criteria, without revealing the provider name.

## Usage

### As a Skill (Manual Agent Usage)
Refer to `SKILL.md` in this directory for instructions on how to manually qualify a user during a conversation.

### On Seren Desktop (Cloud Agent)

The skill is deployed to **Seren Cloud** and can be run from [Seren Desktop](https://github.com/serenorg/seren-desktop).

| Field | Value |
|-------|-------|
| **Agent ID** | `04244f5a-1133-4cd7-87cd-4ce80f62636f` |
| **Name** | `lender-loan` |
| **Skill slug** | `lender-loan` |
| **Run endpoint** | `https://api.serendb.com/publishers/seren-cloud/agents/04244f5a-1133-4cd7-87cd-4ce80f62636f/runs` |

**In Seren Desktop:**

1. **Sign in** with the Seren account that owns the lender-loan agent (same org).
2. Ensure **Seren** or **Gateway** MCP is enabled in Settings so the AI can use cloud agent tools.
3. In chat, ask to run the agent, for example:
   - *"Run the lender-loan agent."*
   - *"List my cloud agents and run the one named lender-loan."*
4. The AI can use **list_cloud_agents** (you should see `lender-loan`) and **run_cloud_agent** with the agent ID above.
5. To qualify someone, send input in the run body: `{ "state": null, "input": { "asset_type": "equity", "asset_value": "1000000", "loan_amount": "600000" } }`. Pass the returned `state` into the next run to advance phases.

See **SKILL.md** for the full workflow (asset types, phases, and run request/response format).

### As a Deployed Script (Local)

The implementation logic resides in `deploy/lender-loan/index.ts`. It can be run as a standalone script or redeployed to Seren Cloud.

To run locally:
```bash
cd deploy/lender-loan
npm install
npm start
```
(Or use `npx tsx index.ts` directly. Send JSON to stdin: `{ "state": null, "input": { ... } }`.)

To redeploy to Seren Cloud (from repo root, requires `SEREN_API_KEY` in `.env.local`):
```powershell
.\scripts\deploy-lender-loan-on-demand.ps1        # mode=always_on
.\scripts\deploy-lender-loan-on-demand.ps1 cron  # mode=cron
```

## Qualification Criteria (Confidential)

- **Equity Loans:** Min $500k loan (~$715k asset value).
- **Crypto Loans:** Min $1M loan (~$1.45M asset value).
- **Block Trades:** Min $50M size.

## Notification

If a user qualifies, the skill instructs the agent (or script) to notify `erik@volume.finance`.  
The script implementation logs the email content to stdout.
