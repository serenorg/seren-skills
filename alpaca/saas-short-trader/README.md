# Alpaca SaaS Short Trader

Production-oriented autonomous skill for shorting AI-vulnerable SaaS equities via Alpaca.

## Directory

```
alpaca/saas-short-trader/
├── SKILL.md
├── README.md
├── requirements.txt
├── .env.example
├── .gitignore
├── config.example.json
└── scripts/
    ├── dry_run_checklist.md
    ├── dry_run_prompt.txt
    ├── run_agent_server.py
    ├── setup_cron.py
    ├── setup_serendb.py
    ├── strategy_engine.py
    ├── serendb_storage.py
    ├── seren_client.py
    ├── self_learning.py
    ├── serendb_schema.sql
    └── self_learning_schema.sql
```

## Quick Start

```bash
python3 -m pip install -r requirements.txt
cp .env.example .env
cp config.example.json config.json
python3 scripts/setup_serendb.py --dsn "$SERENDB_DSN"
python3 scripts/strategy_engine.py --dsn "$SERENDB_DSN" --run-type scan --mode paper-sim --strict-required-feeds --config config.json
```

## Continuous Operation

```bash
SERENDB_DSN="$SERENDB_DSN" SAAS_SHORT_TRADER_WEBHOOK_SECRET="$SAAS_SHORT_TRADER_WEBHOOK_SECRET" \
python3 scripts/run_agent_server.py --port 8787
```

```bash
python3 scripts/setup_cron.py \
  --runner-url "https://YOUR_PUBLIC_RUNNER_URL" \
  --webhook-secret "$SAAS_SHORT_TRADER_WEBHOOK_SECRET"
```

## Notes

- Use `paper-sim` first.
- Self-learning promotion requires gate checks; it does not auto-promote to live.
- Use `scripts/dry_run_prompt.txt` for a single copy/paste test run.

## Disclaimers

### Legal and Regulatory

- This software is for informational and execution-automation purposes only.
- You are responsible for ensuring your use complies with all applicable laws, broker rules, exchange rules, and jurisdictional restrictions.
- Some strategies, instruments, and execution patterns may be restricted in certain jurisdictions or account types.

### Investment and Risk

- This is not investment advice, portfolio advice, legal advice, or tax advice.
- Trading equities, especially short-selling, involves substantial risk including unlimited loss potential, recalls, borrow constraints, and rapid market moves.
- Past performance, simulations, and paper results do not guarantee future results.
- You are solely responsible for all trading decisions and outcomes.

### Tax

- Trades, short-sale proceeds, borrow fees, dividends-in-lieu, and realized/unrealized PnL may have tax consequences.
- You are responsible for all tax reporting and payments in your jurisdiction.
- Consult a qualified tax professional before live deployment.
