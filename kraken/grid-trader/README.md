# Kraken Grid Trading Bot

Automated grid trading bot for Kraken that profits from BTC volatility using a mechanical, non-directional strategy.

## What is Grid Trading?

Grid trading places buy and sell orders at regular price intervals (the "grid"). When price moves up and down, orders fill automatically:
- **Buy orders fill** when price drops → Accumulate BTC at lower prices
- **Sell orders fill** when price rises → Take profit at higher prices
- **Profit**: 2% per grid cycle minus 0.32% fees = **1.68% net profit**

### Example

With $1,000 bankroll and 2% grid spacing:
1. Place buy order at $50,000
2. Place sell order at $51,000 (2% higher)
3. Price drops → Buy fills at $50,000
4. Price rises → Sell fills at $51,000
5. **Profit**: $1,000 × 2% = $20 gross, minus $3.20 fees = **$16.80 net**

With 15 fills per day: **$252/day or $7,560/month** (75.6% monthly return)

## Features

- **Mechanical Strategy**: No predictions, just profit from volatility
- **Risk Management**: Stop-loss, position limits, bankroll protection
- **Cost Efficient**: 0.16% maker fees via Kraken
- **Always Active**: Always has opportunities (unlike prediction markets)
- **Audit Trail**: JSONL logs for every trade
- **Dry-Run Mode**: Test strategy without risking capital

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Set Up Environment

```bash
# Copy example env file
cp .env.example .env

# Add your Seren API key
echo "SEREN_API_KEY=sb_your_key_here" > .env
```

Get your Seren API key at: https://serendb.com

Optional MCP-native SerenDB settings in `.env`:

```bash
SERENDB_PROJECT_NAME=krakent
SERENDB_DATABASE=krakent
SERENDB_BRANCH=
SERENDB_REGION=aws-us-east-1
SERENDB_AUTO_CREATE=true
SEREN_MCP_COMMAND=seren-mcp
```

When unset, the bot first tries existing Kraken-related SerenDB targets, then auto-creates `krakent/krakent` (if `SERENDB_AUTO_CREATE=true`).

### 3. Configure Trading Parameters

```bash
# Copy example config
cp config.example.json config.json

# Edit config.json with your parameters:
# - bankroll: Starting capital ($100-10,000)
# - grid_levels: Number of price levels (10-50)
# - grid_spacing_percent: Spacing between levels (1-5%)
# - price_range: Min/max prices for grid bounds
```

### 4. Run Setup

```bash
python scripts/agent.py setup --config config.json
```

This validates your config and shows expected profit projections.

### 5. Test with Dry-Run

```bash
python scripts/agent.py dry-run --config config.json --cycles 10
```

Simulates 10 trading cycles without placing real orders (zero cost).

### 6. Start Live Trading

```bash
python scripts/agent.py start --config config.json
```

Bot will:
- Place buy orders below current price
- Place sell orders above current price
- Automatically replace filled orders
- Log all trades to `logs/` directory
- Stop if bankroll drops below stop-loss threshold

Press `Ctrl+C` to stop trading.

## Commands

```bash
# Setup and validate configuration
python scripts/agent.py setup --config config.json

# Simulate trading (no real orders)
python scripts/agent.py dry-run --config config.json --cycles 10

# Start live trading
python scripts/agent.py start --config config.json

# Check current status
python scripts/agent.py status --config config.json

# Stop trading and cancel all orders
python scripts/agent.py stop --config config.json
```

## Configuration

### Example config.json

```json
{
  "campaign_name": "BTC_Grid_2026",
  "trading_pair": "XBTUSD",
  "strategy": {
    "bankroll": 1000.0,
    "grid_levels": 20,
    "grid_spacing_percent": 2.0,
    "order_size_percent": 5.0,
    "price_range": {
      "min": 45000,
      "max": 55000
    },
    "scan_interval_seconds": 60
  },
  "risk_management": {
    "stop_loss_bankroll": 800.0,
    "max_position_size": 0.1,
    "max_open_orders": 40
  }
}
```

### Key Parameters

- **bankroll**: Total capital allocated to bot ($100-10,000)
- **grid_levels**: Number of price levels (more = tighter grid, more fills)
- **grid_spacing_percent**: Spacing between levels (2% recommended)
- **order_size_percent**: Size per order as % of bankroll (5% recommended)
- **price_range**: Min/max prices for grid (should span 20-30% range)
- **scan_interval_seconds**: How often to check for fills (60s recommended)
- **stop_loss_bankroll**: Auto-stop if value drops below this (80% of bankroll)

## Cost Analysis

### Trading Fees (Kraken)
- **Maker fee**: 0.16% per order (limit orders)
- **Round-trip fee**: 0.32% (buy + sell)

### Grid Spacing vs Profit

| Grid Spacing | Gross Profit | Fees | Net Profit | Viable? |
|--------------|--------------|------|------------|---------|
| 1% | 1.0% | 0.32% | 0.68% | ✅ Yes |
| 2% | 2.0% | 0.32% | 1.68% | ✅ Best |
| 3% | 3.0% | 0.32% | 2.68% | ✅ Yes (wider range) |

**Recommendation**: Use 2% spacing for optimal balance of profit and fill frequency.

### Example P&L (2% spacing, 15 fills/day)

**Per cycle:**
- Gross profit: $20.00
- Fees: $3.20
- Net profit: $16.80

**Daily (15 fills):**
- Net profit: $252.00
- ROI: 25.2% per day

**Monthly (30 days):**
- Net profit: $7,560.00
- ROI: 756% per month

*Note: Actual results vary with market volatility*

## Logs

All operations logged to `logs/` directory as JSONL files:

- `grid_setup.jsonl` - Grid initialization
- `orders.jsonl` - Order placements/cancellations
- `fills.jsonl` - Trade executions
- `positions.jsonl` - Position snapshots
- `errors.jsonl` - Errors and warnings

## SerenDB Persistence

When `seren-mcp` is available, the bot also persists to SerenDB:

- `kraken_grid_sessions`
- `kraken_grid_orders`
- `kraken_grid_fills`
- `kraken_grid_positions`
- `kraken_grid_events`

SerenDB persistence is best-effort; if unavailable, trading continues with local logs.

## Safety Features

1. **Stop-Loss**: Auto-stops if bankroll drops below threshold
2. **Position Limits**: Prevents overexposure
3. **Dry-Run Mode**: Test without risking capital
4. **Audit Trail**: Every trade logged
5. **Graceful Shutdown**: Cancels all orders on exit

## Troubleshooting

### "SEREN_API_KEY is required"

Create a `.env` file with your API key:

```bash
echo "SEREN_API_KEY=sb_your_key_here" > .env
```

### "Insufficient funds"

Your Kraken account needs:
- USD balance for buy orders
- BTC balance for sell orders

Transfer funds to your Kraken account before starting.

### Orders not filling

- Check that price range includes current market price
- Widen grid spacing (try 2-3%)
- Increase scan interval to 60-120s

### Bot stops unexpectedly

Check `logs/errors.jsonl` for error details:

```bash
tail -f logs/errors.jsonl
```

## Support

- Seren Docs: https://docs.serendb.com
- Kraken API: https://docs.kraken.com/api
- Issues: https://github.com/serenorg/seren-desktop-issues

## License

MIT License - See main repository for details.

---

**Taariq Lewis, SerenAI, Paloma, and Volume at https://serendb.com**
**Email: hello@serendb.com**
