# pk-lead-intelligence

PK Lead Intelligence skill — daily enrichment + weekly status pipeline for
the Packaging division. End-user-facing documentation lives in `SKILL.md`;
this README is for the engineer cloning the repo.

## Local setup

1. Python 3.11+
2. `cd autumn/pk-lead-intelligence`
3. `python3 -m venv .venv && source .venv/bin/activate`
4. `pip install -r requirements.txt`
5. `playwright install chromium`
6. `cp .env.example .env` and fill in
7. `cp config.example.json config.json` and adjust

See the implementation plan checked in alongside the project for the full
phase-by-phase breakdown.
