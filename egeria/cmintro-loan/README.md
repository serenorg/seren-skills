# lender Loan Qualification Skill

This skill qualifies potential borrowers for non-recourse stock/crypto loans and institutional block trades based on lender's criteria, without revealing the provider name.

## Usage

### As a Skill (Manual Agent Usage)
Refer to `SKILL.md` in this directory for instructions on how to manually qualify a user during a conversation.

### As a Deployed Script
The implementation logic resides in `deploy/lender-loan/index.ts`. It can be run as a standalone script or deployed as a cloud function.

To run locally:
```bash
cd deploy/lender-loan
npm install
npm start
```
(Or use `npx tsx index.ts` directly)

## Qualification Criteria (Confidential)

- **Equity Loans:** Min $500k loan (~$715k asset value).
- **Crypto Loans:** Min $1M loan (~$1.45M asset value).
- **Block Trades:** Min $50M size.

## Notification

If a user qualifies, the skill instructs the agent (or script) to notify `erik@volume.finance`.
The script implementation logs the email content to stdout.
