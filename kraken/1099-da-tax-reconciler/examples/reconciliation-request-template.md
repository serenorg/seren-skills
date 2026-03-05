# Reconciliation Request Template

Use this prompt format to activate the skill with enough detail for high-quality output.

```text
Reconcile my Kraken crypto transactions to my 1099-DA and prepare a Form 8949 readiness report.

Tax year: <YYYY>
Exchange/Broker(s): Kraken
Tax software: <name>
Accounting method: <FIFO | HIFO | Spec ID | other>
Timezone basis: <e.g., UTC or America/New_York>

SerenDB setup:
- Logged into Seren Desktop: <yes/no>
  (No API key or .env needed -- MCP handles authentication automatically)

Enable features:
- 1099da-normalizer: <yes/no>
- cost-basis-resolver: <yes/no>
- reconciliation-audit: <yes/no>

Files provided:
1) 1099-DA export: <path or description>
2) Tax software disposals export: <path or description>
3) Optional supporting files (wallet exports, transfer logs): <path or description>

Output needed:
- Matched vs unmatched summary
- Proceeds, basis, and gain/loss deltas
- Row-level discrepancy table with recommended fixes
- Final 8949 readiness checklist
- SerenDB persistence summary (what was saved)
```
