---
name: carf-dac8-crypto-asset-reporting
display-name: "Kraken CARF DAC8 Reporting"
description: "Reconcile CARF/DAC8 exchange-reported crypto transactions against user records, including transfer tracking and optional 1099-DA bridge mode."
---

# CARF / DAC8 Crypto Asset Reporting

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

Local-first reconciliation skill for OECD CARF and EU DAC8 reporting data.

## When to Use

- reconcile exchange CARF XML against my tax software export
- validate DAC8 records for e-money and high-value NFT coverage
- detect multi-jurisdiction crypto reporting obligations
- combine 1099-DA and CARF records in one reconciliation workflow

## What This Skill Provides

- CARF XML parser and DAC8 extension parser
- CASP CSV and user CSV normalization into a common transaction schema
- Matching engine with exact/fuzzy matching and configurable tolerances
- Transfer-specific reconciliation tracking
- Multi-jurisdiction detection with deadline notes
- Optional 1099-DA bridge mode and dual-report detection
- CPA escalation package generation for material or judgment-sensitive items
- Optional SerenDB persistence for audit trails (`SERENDB_URL`)

## API Key Setup

Before running this skill, check for an existing Seren API key in this order:

1. **Seren Desktop auth** — if the skill is running inside Seren Desktop, the runtime injects `API_KEY` automatically. Check: `echo $API_KEY`. If set, no further action is needed.
2. **Existing `.env` file** — check if `SEREN_API_KEY` is already set in the skill's `.env` file. If set, no further action is needed.
3. **Shell environment** — check if `SEREN_API_KEY` is exported in the current shell. If set, no further action is needed.

**Only if none of the above are set**, register a new agent account:

```bash
curl -sS -X POST "https://api.serendb.com/auth/agent" \
  -H "Content-Type: application/json" \
  -d '{"name":"carf-dac8-crypto-asset-reporting"}'
```

Extract the API key from the response at `.data.agent.api_key` — **this key is shown only once**. Write it to the skill's `.env` file:

```env
SEREN_API_KEY=<the-returned-key>
```

Verify:

```bash
curl -sS "https://api.serendb.com/auth/me" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

**Do not create a new account if a key already exists.** Creating a duplicate account results in a $0-balance key that overrides the user's funded account.

Reference: [https://docs.serendb.com/skills.md](https://docs.serendb.com/skills.md)

## Setup

1. Copy `.env.example` to `.env` and set credentials.
2. Copy `config.example.json` to `config.json`.
3. Install dependencies:
   - `pip install -r requirements.txt`
4. Run reconciliation:
   - `python scripts/agent.py run --config config.json --carf-report path/to/report.xml --user-records path/to/user.csv --accept-risk-disclaimer`
5. Optional bridge mode:
   - add `--bridge-1099da path/to/1099da.csv`

## Workflow Summary

1. Validate config and enforce first-run disclaimer acknowledgment.
2. Ensure `SEREN_API_KEY` exists (validate existing or auto-register).
3. Parse CARF/DAC8 and user records into a common schema.
4. Detect applicable jurisdictions and reporting deadlines.
5. Match, classify discrepancies, and detect escalation candidates.
6. Optionally persist data and reconciliation outputs to SerenDB.
7. Emit markdown + JSON reports under `state/reports/`.

## Required Disclaimers

IMPORTANT DISCLAIMERS — READ BEFORE USING

1. NOT TAX OR LEGAL ADVICE: This skill is a reconciliation utility. It does not provide tax, legal, or accounting advice.
2. USER ACCOUNTABILITY: You are responsible for final tax filings and jurisdiction-specific compliance.
3. DATA QUALITY LIMITS: Input files can be incomplete or inconsistent. Matching results may require manual review.
4. LOCAL-FIRST PROCESSING: Files are processed locally on your machine. No transaction files are sent to SerenAI services.
5. CPA ESCALATION: Material discrepancies and judgment-sensitive items should be reviewed by a licensed CPA.
6. SOFTWARE PROVIDED AS-IS: No warranty is provided; validate outputs before filing.
