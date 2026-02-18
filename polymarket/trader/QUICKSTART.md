# Quick Start Guide

Get the Polymarket Trading Bot running in 5 minutes.

## Prerequisites

- Python 3.9+
- $550+ total budget (see [SKILL.md Phase 4](SKILL.md#phase-4-fund-your-wallets) for why)
- Seren API key from [app.serendb.com/settings/api-keys](https://app.serendb.com/settings/api-keys)

## One-Command Setup (Recommended)

```bash
./scripts/setup_test.sh
```

This script will:
- ✅ Install Python dependencies
- ✅ Create `.env` with your SEREN_API_KEY
- ✅ Create `config.json` with safe defaults
- ✅ Run syntax validation tests
- ✅ Run dry-run tests

## Manual Setup

If the script doesn't work:

### 1. Install Dependencies

```bash
pip3 install -r requirements.txt
```

### 2. Create `.env`

```bash
cp .env.example .env
```

Edit `.env` and add your SEREN_API_KEY:

```bash
SEREN_API_KEY=your_actual_key_here
```

For testing, you can use mock Polymarket credentials. For live trading, get real ones from [polymarket.com/settings/api](https://polymarket.com/settings/api).

### 3. Create `config.json`

```bash
cp config.example.json config.json
```

Review the settings - defaults are safe for testing.

### 4. Run Tests

```bash
# Syntax validation (no credentials needed)
python3 scripts/test_syntax.py

# Dry-run test (needs SEREN_API_KEY)
python3 scripts/test_dry_run.py
```

## Running the Bot

### Dry-Run Mode (Recommended First)

Test without placing real trades:

```bash
python3 scripts/agent.py --config config.json --dry-run
```

The bot will:
- ✅ Scan real markets
- ✅ Analyze opportunities
- ✅ Calculate position sizes
- ❌ NOT place actual trades

### Live Trading Mode

⚠️ **Only use this with real Polymarket credentials and $550+ budget**

1. Start the agent server:

```bash
python3 scripts/run_agent_server.py --config config.json
```

2. In another terminal, setup autonomous scheduling:

```bash
python3 scripts/setup_cron.py --url http://localhost:8080/run --schedule "*/120 * * * *"
```

This creates a cron job that triggers the bot every 2 hours.

## Monitoring

View logs in real-time:

```bash
tail -f logs/trading_*.log
```

## Troubleshooting

### "ModuleNotFoundError"

```bash
pip3 install -r requirements.txt
```

### "SEREN_API_KEY not found"

Check your `.env` file:

```bash
cat .env | grep SEREN_API_KEY
```

Make sure it's set and the file is in the same directory as the bot scripts.

### "SSL Warning about LibreSSL"

This is harmless - just a compatibility warning. The bot will still work.

### Bot Exits Immediately

Check if `config.json` exists:

```bash
ls -la config.json
```

If missing, run `./scripts/setup_test.sh` or `cp config.example.json config.json`.

## Next Steps

1. Read [SKILL.md](SKILL.md) for full documentation
2. Review Phase 4 budget requirements - you need $550+ to trade profitably
3. Run dry-run tests to learn the system
4. When ready, fund your wallets and switch to live mode

## Support

- Documentation: [SKILL.md](SKILL.md)
- Issues: File a GitHub issue or contact Seren support
- Community: Join the Seren Discord
