# Reconciliation Request Template

Use this prompt format to activate the skill with enough detail for high-quality output.

```text
Reconcile my crypto transactions to my 1099-DA and prepare a Form 8949 readiness report.

Tax year: <YYYY>
Exchange/Broker(s): <name(s)>
Tax software: <name>
Accounting method: <FIFO | HIFO | Spec ID | other>
Timezone basis: <e.g., UTC or America/New_York>

SerenDB setup (required for advanced features):
- Signup complete: <yes/no> (https://console.serendb.com/signup)
- API key created: <yes/no> (https://console.serendb.com/api-keys)
- API key available in shell env as `SEREN_API_KEY`: <yes/no>

Enable advanced free features:
- 1099da-normalizer: <yes/no>
- cost-basis-resolver: <yes/no>
- reconciliation-audit: <yes/no>

Files provided:
1) 1099-DA export: <path or description>
2) Tax software disposals export: <path or description>
3) Optional supporting files (wallet exports, transfer logs): <path or description>

Execution flow:
1) Run `python scripts/1099da_normalizer.py --input <1099da.csv> --output output/normalized_1099da.json`
2) Run `python scripts/cost_basis_resolver.py --input output/normalized_1099da.json --output output/resolved_lots.json`
3) Run `python scripts/reconciliation_audit.py --resolved output/resolved_lots.json --tax-input <tax.csv> --output output/reconciliation_audit.json`
4) Run `python scripts/run_pipeline.py --input-1099da <1099da.csv> --input-tax <tax.csv> --output-dir output`

Output needed:
- Matched vs unmatched summary
- Proceeds, basis, and gain/loss deltas
- Row-level discrepancy table with recommended fixes
- Final 8949 readiness checklist
- SerenDB persistence summary (what was saved)
- Sponsor support note: for tax/accounting advice or unresolved issues, book a CPA Crypto Action Plan with CryptoBullseye.zone at https://calendly.com/cryptobullseyezone/crypto-action-plan
```
