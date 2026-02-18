# Polymarket Trading Bot

Autonomous trading agent for Polymarket prediction markets using the Seren ecosystem.

## Features

- 🔍 Scans Polymarket for trading opportunities
- 🧠 Researches markets using Perplexity AI
- 💡 Estimates fair value using Claude (Anthropic)
- 📊 Executes trades using Kelly Criterion position sizing
- 🔄 Runs autonomously via seren-cron scheduling
- 📈 Tracks positions and P&L

## Quick Start

### 1. Install Dependencies

```bash
cd skills/polymarket-trader
pip3 install -r requirements.txt
```

### 2. Configure Credentials

Copy the example environment file and fill in your credentials:

```bash
cp .env.example .env
```

Edit `.env` and add:
- **SEREN_API_KEY**: Get from [app.serendb.com/settings/api-keys](https://app.serendb.com/settings/api-keys)
- **POLY_API_KEY, POLY_PASSPHRASE, POLY_ADDRESS**: Get from [polymarket.com](https://polymarket.com) (Settings > API Keys > Derive API Key)

### 3. Create Configuration

Copy the example config and customize:

```bash
cp config.example.json config.json
```

Edit `config.json` to set:
- `bankroll`: Total trading capital (e.g., 100.0 for $100)
- `mispricing_threshold`: Minimum edge to trade (0.08 = 8%)
- `max_kelly_fraction`: Max % per trade (0.06 = 6%)
- `max_positions`: Maximum concurrent positions
- `stop_loss_bankroll`: Stop trading if bankroll drops below this

### 4. Test with Dry-Run

Test the bot without placing real trades:

```bash
python3 scripts/agent.py --config config.json --dry-run
```

This will:
- ✅ Scan live markets from Polymarket
- ✅ Research opportunities using Perplexity
- ✅ Estimate fair values using Claude
- ✅ Calculate position sizes
- ❌ NOT place actual trades

### 5. Go Live (⚠️ USE CAUTION)

Once you've tested and are ready to trade with real money:

```bash
python3 scripts/agent.py --config config.json
```

**IMPORTANT**: Only risk what you can afford to lose!

## Project Structure

```
polymarket-trader/
├── scripts/agent.py                 # Main trading bot
├── scripts/seren_client.py          # Seren API client
├── scripts/polymarket_client.py     # Polymarket CLOB API wrapper
├── scripts/kelly.py                 # Kelly Criterion calculator
├── scripts/position_tracker.py      # Position management
├── scripts/logger.py                # Trading logger
├── requirements.txt         # Python dependencies
├── config.json              # Trading configuration (create from example)
├── .env                     # API credentials (create from example)
└── logs/                    # Trading logs
    ├── trades.jsonl         # Trade history
    ├── scan_results.jsonl   # Scan cycle results
    ├── positions.json       # Current positions
    └── notifications.jsonl  # Critical events
```

## How It Works

### 1. Market Scanning
- Fetches active markets from Polymarket
- Filters for tradable opportunities

### 2. Research
- Uses Perplexity to research each market
- Gathers recent news, expert opinions, and analysis

### 3. Fair Value Estimation
- Sends research to Claude (via seren-models)
- Claude estimates true probability with confidence level
- Only trades on "medium" or "high" confidence estimates

### 4. Opportunity Evaluation
- Calculates edge: `|fair_value - market_price|`
- Filters for edge > mispricing_threshold (e.g., 8%)
- Checks position limits and bankroll

### 5. Position Sizing
- Uses Kelly Criterion for optimal sizing
- Formula: `kelly = (fair_value - price) / (1 - price)` for BUY
- Uses quarter-Kelly (divide by 4) for conservatism
- Caps at max_kelly_fraction of bankroll

### 6. Execution
- Places orders via polymarket-trading-serenai publisher
- Tracks positions with entry price and size
- Logs all activity to JSONL files

### 7. Monitoring
- Updates positions with current prices
- Calculates unrealized P&L
- Checks stop loss conditions
- Logs notifications for critical events

## Cost Estimation

### SerenBucks (API Calls)
- Perplexity research: ~$0.01 per market
- Claude fair value estimate: ~$0.01 per market
- Total: ~$0.02 per market scanned

**Daily cost examples:**
- Scanning every 10 minutes: ~$2-5/day (depends on markets scanned)
- Scanning every 30 minutes: ~$0.50-1.50/day

### Polymarket Trading
- Order placement: $0.005 per order
- Order cancellation: $0.002 per cancellation
- Data queries: $0.001 per request

## Risks & Disclaimers

⚠️ **READ THIS CAREFULLY**

### Financial Risks
- You can lose money trading prediction markets
- Past performance does not guarantee future results
- AI estimates may be inaccurate
- Markets can change rapidly
- Slippage and fees reduce returns

### Regulatory Risks
- Polymarket is **NOT available in the United States**
- Prediction markets may be illegal in your jurisdiction
- Some regions classify them as gambling
- You are responsible for compliance with local laws

### Technical Risks
- Software bugs may cause losses
- API failures may prevent trading
- Credential exposure could compromise your wallet

**This bot is provided "as is" without warranty. Use at your own risk.**

## Known Limitations

- **Position closing**: Detects API-closed positions but does not actively execute stop-loss or take-profit exits
- **Notifications**: Events are logged to `notifications.jsonl` but no email or webhook delivery is implemented
- **Backtesting**: No historical replay engine; `scripts/performance.py` analyses past live results only
- **Web dashboard**: No monitoring UI; all data lives in JSONL files and SerenDB tables

## Contributing

This bot is part of the Seren ecosystem. Contributions welcome!

## License

Taariq Lewis, SerenAI, Paloma, and Volume at https://serendb.com
Email: hello@serendb.com
