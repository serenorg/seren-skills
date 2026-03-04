# Kraken Smart DCA Bot

This is a SkillForge-generated Kraken DCA skill scaffold with guardrails and three operating modes.

## Quick Start

1. Copy `.env.example` to `.env` and populate API keys.
2. Copy `config.example.json` to `config.json`.
3. Install deps: `pip install -r requirements.txt`.
4. Run dry mode:
   - `python scripts/agent.py --config config.json`
5. Run live mode explicitly:
   - set `"dry_run": false` in `config.json`
   - `python scripts/agent.py --config config.json --allow-live --accept-risk-disclaimer`

## Notes

- Runtime is intentionally scaffold-level and can be extended with direct Kraken API calls.
- Local runs are persisted to `state/dca_runs.db`.
