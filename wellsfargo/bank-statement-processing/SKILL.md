---
name: bank-statement-processing
description: "Wells Fargo bank statement retrieval skill for Seren Desktop: runtime-authenticated PDF download, transaction parsing, and masked SerenDB sync."
---

# Wells Fargo Bank Statements

## When To Use

- Download Wells Fargo monthly statements as PDFs.
- Parse transactions from statement PDFs.
- Persist masked statement and transaction data into SerenDB.
- Produce reusable cashflow datasets for downstream skills.

## Safety Profile

- Strict execution boundary: read-only.
- Runtime auth mode: prompt each run.
- Never persists username, password, or OTP codes.
- Keeps raw PDFs local only.
- Stores only masked account fields in SerenDB.
- SerenDB sync is enabled by default and auto-resolves DB URL from logged-in Seren context.
- Browser control is MCP-native via SerenDesktop Local Playwright MCP.
- In manual auth mode, the run prompts for an installed browser choice unless `--browser-app` / `--browser-type` are provided.
- Manual auth opens `https://wellsfargo.com/` automatically before handoff.
- After login handoff, the run auto-attempts `Accounts -> View Statements & Documents`.
- Browser execution is split into browser-specific paths:
  - Firefox path preserves the historically stable flow.
  - Chrome path enables isolated recovery/fallback logic for Chrome-only issues.
- The run enforces a single active process lock per artifact directory to prevent multiple browser windows from concurrent runs.
- The run defaults to 12 months of statements (minimum 3).

## Workflow Summary

1. `auth_prompted` asks for username/password at runtime.
2. `playwright_browser` captures selected browser target (prompted in manual mode).
3. `otp_waiting` pauses and prompts for OTP if challenge appears.
4. `authenticated` confirms dashboard/session access.
5. `statement_indexed` discovers statement rows.
6. `pdf_downloaded` downloads PDFs to local artifacts.
7. `pdf_parsed` extracts normalized transaction rows.
8. `classified` applies rules-first + LLM-fallback categorization.
9. `serendb_synced` upserts masked metadata and transaction rows.
10. `complete` writes a user-facing success report.

SerenDB URL resolution order:
1. `WF_SERENDB_URL` if explicitly provided.
2. Logged-in Seren CLI context (`seren env init` with resolved project/branch).
3. Fallback project/branch selection from `seren list-all-databases` using
   `serendb.project_name`, `serendb.branch_name`, and `serendb.database_name` (default `serendb`).

## Quick Start

1. Install dependencies:

```bash
cd examples/migrations/wellsfargo-bank-statements-download
python3 -m pip install -r requirements.txt
cp .env.example .env
cp config.example.json config.json
seren auth
# alternative for non-interactive runs: export SEREN_API_KEY
# export SEREN_API_KEY=sb_...
# optional but recommended in multi-project accounts:
# set serendb.project_id (or serendb.project_name) and/or serendb.database_name in config.json
# optional: set PLAYWRIGHT_MCP_SCRIPT in .env if auto-detect is unavailable
# example: /Applications/SerenDesktop.app/Contents/Resources/embedded-runtime/mcp-servers/playwright-stealth/dist/index.js
```

2. Run end-to-end (read-only):

```bash
python3 scripts/run.py --config config.json --mode read-only --months 12 --out artifacts/wellsfargo
```

3. Resume a prior interrupted run:

```bash
python3 scripts/run.py --config config.json --mode read-only --resume --out artifacts/wellsfargo
```

## Commands

```bash
# End-to-end run
python3 scripts/run.py --mode read-only --months 12 --out artifacts/wellsfargo

# End-to-end run with explicit browser override (skips browser prompt)
python3 scripts/run.py --mode read-only --auth-method manual --browser-app "Google Chrome" --browser-type chrome --months 12 --out artifacts/wellsfargo

# End-to-end run pinned to Firefox stable path
python3 scripts/run.py --mode read-only --auth-method manual --browser-app "Firefox" --browser-type moz-firefox --months 12 --out artifacts/wellsfargo

# End-to-end run with passkey auth (requires local user approval prompt)
python3 scripts/run.py --mode read-only --auth-method passkey --months 12 --out artifacts/wellsfargo

# Parse local PDFs only (skip browser)
python3 scripts/run.py --mode read-only --skip-download --out artifacts/wellsfargo

# Replay SerenDB sync from local artifacts
python3 scripts/run.py --mode read-only --skip-download --replay-serendb <run_id> --out artifacts/wellsfargo
```

## Outputs

- Local PDFs: `artifacts/wellsfargo/pdfs/...`
- Checkpoint state: `artifacts/wellsfargo/state/checkpoint.json`
- Machine report: `artifacts/wellsfargo/reports/<run_id>.json`
- Human summary: `artifacts/wellsfargo/reports/<run_id>.md`
- Transaction export: `artifacts/wellsfargo/exports/<run_id>.transactions.jsonl`

## SerenDB Tables

- `wf_runs`
- `wf_statement_files`
- `wf_transactions`
- `wf_txn_categories`
- `wf_monthly_summary`

## Reusable Views

- `v_wf_latest_statements`
- `v_wf_transactions_clean`
- `v_wf_monthly_cashflow`
