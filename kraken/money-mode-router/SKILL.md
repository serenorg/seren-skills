---
name: money-mode-router
description: "Kraken customer skill that converts user goals into a concrete Kraken action mode (payments, investing, trading, on-chain, automation) and persists each session to SerenDB"
---

# Kraken Money Mode Router

Route users to the best Kraken product flow fast.

Use this skill when a user asks things like:
- "What should I use in Kraken?"
- "Should I trade, invest, pay, or go on-chain?"
- "Give me a plan for my money on Kraken"

## What It Does

1. Captures user intent with a short questionnaire.
2. Scores Kraken money modes against user goals.
3. Returns a primary mode and backup mode.
4. Produces a concrete action checklist users can execute immediately.
5. Stores session, answers, recommendations, and action plan in SerenDB.

## Modes

- `payments` -> Krak-focused everyday money movement
- `investing` -> multi-asset portfolio building
- `active-trading` -> hands-on market execution
- `onchain` -> Kraken spot funding endpoints for deposits, withdrawals, and wallet transfers
- `automation` -> rules-based, repeatable execution

## Setup

1. Copy `.env.example` to `.env`.
2. Ensure `seren-mcp` is available locally and authenticated (Seren Desktop login context).
3. Set `SEREN_API_KEY` (required for Kraken account context and MCP auth when running standalone).
4. Optionally set MCP DB target env vars (`SERENDB_PROJECT_NAME`, `SERENDB_DATABASE`, optional branch/region).
   - If `SERENDB_DATABASE` is not set, the router first tries to reuse an existing Kraken-related database.
   - If none exists, it auto-creates `krakent` project + `krakent` database (when `SERENDB_AUTO_CREATE=true`).
5. Copy `config.example.json` to `config.json`.
6. Install dependencies: `pip install -r requirements.txt`.

## Commands

```bash
# Initialize SerenDB schema
python scripts/agent.py init-db

# Interactive recommendation flow
python scripts/agent.py recommend --config config.json --interactive

# Recommendation flow from JSON answers file
python scripts/agent.py recommend --config config.json --answers-file answers.json
```

## Output

The agent returns:
- primary mode
- backup mode
- confidence score
- top reasons
- action checklist
- API-backed mode coverage
- session id for querying SerenDB history

## Data Model (SerenDB)

Tables created by `init-db`:
- `kraken_skill_sessions`
- `kraken_skill_answers`
- `kraken_skill_recommendations`
- `kraken_skill_actions`
- `kraken_skill_events`

## Notes

- This skill does not implement compliance policy logic. It routes user intent and lets Kraken API permissions enforce availability.
- The router only recommends modes backed by currently configured publishers.
