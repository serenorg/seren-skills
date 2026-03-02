# Wells Fargo Bank Statements

End-to-end read-only automation for Wells Fargo statements:

- prompts for credentials and OTP at runtime,
- downloads PDFs locally,
- parses and categorizes transactions,
- persists masked metadata in SerenDB,
- emits reports and JSONL exports reusable by other skills.

Browser automation is MCP-native and expects SerenDesktop Local Playwright MCP.
In manual auth mode, the run prompts you to choose an installed browser unless you pass `--browser-app` / `--browser-type`.
Only one active run is allowed per artifact directory to prevent multiple concurrent browser windows.
The run enforces a minimum of 3 months of statements.

## Install

```bash
python3 -m pip install -r requirements.txt
cp .env.example .env
cp config.example.json config.json
# ensure Seren CLI is authenticated so DB URL can auto-resolve
seren auth
# alternative for non-interactive runs: export SEREN_API_KEY
# export SEREN_API_KEY=sb_...
# optional but recommended in multi-project accounts:
# set serendb.project_id (or serendb.project_name) and/or serendb.database_name in config.json
# optional: set PLAYWRIGHT_MCP_SCRIPT in .env if auto-detect is unavailable
# example: /Applications/SerenDesktop.app/Contents/Resources/embedded-runtime/mcp-servers/playwright-stealth/dist/index.js
```

## Run

```bash
python3 scripts/run.py --config config.json --mode read-only --months 3 --out artifacts/wellsfargo
```

SerenDB URL resolution order (default sync path):
1. `WF_SERENDB_URL` if explicitly set.
2. `seren env init` with resolved `project_id` / `branch_id` from context/config.
3. If context is missing, best-match fallback from `seren list-all-databases` using
   `serendb.project_name`, `serendb.branch_name`, and `serendb.database_name` (default `serendb`).

Passkey mode (human-in-the-loop approval on this device):

```bash
python3 scripts/run.py --config config.json --mode read-only --auth-method passkey --months 3 --out artifacts/wellsfargo
```

Manual handoff mode (you complete login in the opened browser, then resume):

```bash
python3 scripts/run.py --config config.json --mode read-only --auth-method manual --months 3 --out artifacts/wellsfargo
```

Manual mode behavior:
- Browser auto-opens to `https://wellsfargo.com/` (no URL typing required).
- After login handoff, the agent auto-attempts `Accounts -> View Statements & Documents`.
- Includes browser-specific recovery paths for both Firefox and Chrome-family browsers.

Manual handoff mode with explicit browser selection (skips prompt):

```bash
python3 scripts/run.py --config config.json --mode read-only --auth-method manual --browser-app "Google Chrome" --browser-type chrome --months 3 --out artifacts/wellsfargo
```

Recommended: attach to your real Chrome/Brave session over CDP (avoids `Google Chrome for Testing`).

Start Chrome with remote debugging:

```bash
open -na "Google Chrome" --args --remote-debugging-port=9222 --user-data-dir="$HOME/.wf-cdp-profile"
```

Run with CDP attach:

```bash
python3 scripts/run.py --config config.json --mode read-only --auth-method manual --months 3 --out artifacts/wellsfargo --cdp-url http://127.0.0.1:9222 --browser-app "Google Chrome" --browser-type chrome
```

If you still want MCP-launched browser mode, force headed launch:

```bash
PLAYWRIGHT_MCP_HEADED=1 python3 scripts/run.py --config config.json --mode read-only --auth-method manual --months 3 --out artifacts/wellsfargo
```

## Privacy

- No credential persistence.
- No OTP persistence.
- Local PDFs only.
- Masked account fields in database records.
- SerenDB sync defaults to enabled and auto-resolves `WF_SERENDB_URL` from your logged-in Seren context.

## Files

- `scripts/run.py` orchestrator and checkpoints
- `scripts/wf_download.py` MCP-native Playwright login/index/download
- `scripts/pdf_extract.py` PDF transaction extraction
- `scripts/categorize.py` rules + LLM-fallback classification
- `scripts/serendb_load.py` masked upserts to SerenDB
- `scripts/report.py` run reports and exports
- `sql/schema.sql` tables
- `sql/views.sql` cross-skill views
