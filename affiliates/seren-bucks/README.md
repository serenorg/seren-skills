# Seren Bucks

Review-first skill bundle for operating a single Seren Bucks affiliate program.

## What this package includes

- `SKILL.md` with the v1 operating contract
- `config.example.json` with one program, one tracked link, and manual-review defaults
- `serendb_schema.sql` with CRM, approval, DNC, and digest tables
- `scripts/` runtime stubs for bootstrap, sync, ranking, drafting, send-approval, reconciliation, and digest assembly
- `references/` docs for the state machine, provider mapping, schema summary, and output contract

## Local commands

```bash
python3 scripts/agent.py --config config.json
python3 scripts/agent.py --config config.json --command bootstrap
python3 scripts/agent.py --config config.json --command digest
```

The runtime is intentionally stubbed in v1 packaging. The contract, schema, and operator workflow are the primary deliverables.
