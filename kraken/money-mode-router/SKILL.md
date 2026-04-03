---
name: money-mode-router
display-name: "Kraken Money Mode Router"
description: "Kraken customer skill that converts user goals into a concrete Kraken action mode (payments, investing, trading, on-chain, automation) and persists each session to SerenDB"
---

# Kraken Money Mode Router

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

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

## API Key Setup

Before running this skill, check for an existing Seren API key in this order:

1. **Seren Desktop auth** — if the skill is running inside Seren Desktop, the runtime injects `API_KEY` automatically. Check: `echo $API_KEY`. If set, no further action is needed.
2. **Existing `.env` file** — check if `SEREN_API_KEY` is already set in the skill's `.env` file. If set, no further action is needed.
3. **Shell environment** — check if `SEREN_API_KEY` is exported in the current shell. If set, no further action is needed.

**Only if none of the above are set**, register a new agent account:

```bash
curl -sS -X POST "https://api.serendb.com/auth/agent" \
  -H "Content-Type: application/json" \
  -d '{"name":"money-mode-router"}'
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

1. Copy `.env.example` to `.env`.
2. Ensure `seren-mcp` is available locally and authenticated (Seren Desktop login context).
3. Set `SEREN_API_KEY` (required for Kraken account context and MCP auth when running standalone).
4. Optionally set MCP DB target env vars (`SERENDB_PROJECT_NAME`, `SERENDB_DATABASE`, optional branch/region).
   - If `SERENDB_DATABASE` is not set, the router first tries to reuse an existing Kraken-related database.
   - If none exists, it auto-creates `krakent` project + `krakent` database (when `SERENDB_AUTO_CREATE=true`).
5. Copy `config.example.json` to `config.json`.
6. Install dependencies: `pip install -r requirements.txt`.
7. Optional publisher overrides:
   - `KRAKEN_TRADING_PUBLISHER` (default `kraken-trading`)
   - `KRAKEN_TRADING_FALLBACK_PUBLISHER` (default `kraken-spot-trading`)
   - Legacy alias: `KRAKEN_SPOT_PUBLISHER` (treated as fallback)

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

## Disclaimer

This skill provides informational routing recommendations for Kraken products. It does not constitute financial, investment, or tax advice. Cryptocurrency and digital assets involve substantial risk of loss. Past performance does not guarantee future results. You are solely responsible for evaluating the suitability of any product or strategy for your situation. Consult a licensed financial advisor before acting on any recommendation.
