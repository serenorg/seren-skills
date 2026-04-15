# seren-affiliate

Lean partner-link distribution skill for the [seren-affiliates](https://github.com/serenorg/seren-affiliates) program portfolio. Generated from [`seren-skillforge/examples/seren-affiliate`](https://github.com/serenorg/seren-skillforge/tree/main/examples/seren-affiliate) and published here.

## What it does

For one publisher program per run, the skill:

1. Bootstraps the operator's affiliate identity with `seren-affiliates` (registering on first run).
2. Caches joined programs in `serendb` (database `seren_affiliate`).
3. Ingests contacts from a pasted list or a Gmail / Outlook address book.
4. Drafts a single pitch for the selected program via `seren-models`, gated by one operator approval.
5. Sends through Gmail (preferred) or Microsoft Outlook, enforcing per-program dedupe, a global unsubscribe list, and a daily cap (default 10, hard-capped at 25).
6. Reports local distribution metrics joined with live conversion and commission stats from `seren-affiliates`.

## Placement

Published at `seren-skills/affiliates/seren`. Complement to [`affiliates/seren-bucks`](../seren-bucks/SKILL.md) (campaign-specific review-first outreach). Shared `family: affiliate-v1` metadata.

## Getting started

```bash
cp .env.example .env
# Set SEREN_API_KEY in .env

python3 scripts/agent.py --command bootstrap
python3 scripts/agent.py --command status
python3 scripts/agent.py --command run --config config.example.json
```

`config.example.json` is safe to copy to `config.json` and edit. Inputs are documented in [SKILL.md](SKILL.md).

## Layout

```
SKILL.md                         Claude-facing skill documentation
serendb_schema.sql               Database schema for the seren_affiliate db
requirements.txt                 Pytest only; the runtime uses stdlib
config.example.json              Example input config
.env.example                     Required / optional env vars
scripts/
  agent.py                       Dispatcher for bootstrap/sync/run/draft/send/status/block
  common.py                      Shared utilities, DEFAULT_CONFIG, placeholder contract
  bootstrap.py                   Auth, profile register-or-fetch
  sync.py                        Joined programs refresh + select_program
  ingest.py                      Contact sourcing + provider resolve + eligibility + cap
  draft.py                       LLM pitch drafting + approval gate
  send.py                        Merge + per-contact send (Gmail preferred)
  status.py                      Live stats fetch + report rendering
  block.py                       Operator-managed unsubscribe
references/
  prompts/draft_pitch.md         seren-models prompt contract
  state-machine.md               Step DAG and command subgraphs
  provider-mappings.md           Publisher endpoints and paths
tests/
  test_smoke.py                  Invariants: dedupe, cap, approval gate, footer
  fixtures/                      Happy-path, failure, dry-run-guard, policy-violation fixtures
```

## Rollout phases

- **Phase 1 (shipped).** Operator-managed blocklist only. Unsubscribe link in the footer is a documented placeholder; operator removes recipients manually via `command: block`.
- **Phase 2.** Requires a new public `GET /unsubscribe/[agent_id]/[token]` route and `GET /public/unsubscribes?agent_id=...&since=...` read API on `seren-affiliates-website` — tracked in serenorg/seren-affiliates-website#36. Once that ships, `sync` mirrors remote opt-outs into the local `unsubscribes` table by joining returned tokens against local `distributions` to resolve `token → email`. `seren-affiliates` (the backend) is intentionally **not** involved and stores no recipient PII.

## Regenerating from the spec

Edit `seren-skillforge/examples/seren-affiliate/skill.spec.yaml`, validate with `python -m skillforge validate --spec ...`, then re-release with `python -m skillforge release --spec ... --target ../seren-skills --resolve-publishers --require-api-key --create-pr`.
