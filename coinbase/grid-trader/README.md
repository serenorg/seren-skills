# Coinbase Grid Trading Bot

Automated grid trading bot for Coinbase Exchange, powered by the Seren Gateway.

## What is Grid Trading?

Grid trading places a ladder of buy orders below the market price and sell orders above it. When a buy fills, a sell is placed one spacing above it. When a sell fills, a buy is placed one spacing below. Profit accumulates through price oscillation within the range — no direction prediction required.

**Example (BTC-USD, $50 order, 2% spacing at $100,000):**

```
$102,000 — SELL  → fills → place buy at $100,000
$101,000 — SELL  → fills → place buy at $99,000
$100,000 — reference price
 $99,000 — BUY   → fills → place sell at $101,000
 $98,000 — BUY   → fills → place sell at $100,000
```

At 0.40% Coinbase maker fees and $50/order:
- Buy qty at $100,000: 0.0005 BTC
- Gross profit/cycle: 0.0005 × $2,000 = $1.00
- Fees/cycle: ($50 + $51) × 0.40% ≈ $0.40
- **Net profit/cycle: ~$0.60**

---

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure credentials

```bash
cp .env.example .env
```

Edit `.env`:

```
SEREN_API_KEY=sb_...        # Get at app.serendb.com → API Keys
CB_ACCESS_KEY=...           # Coinbase Exchange API key
CB_ACCESS_SECRET=...        # Base64-encoded secret
CB_ACCESS_PASSPHRASE=...    # Passphrase set when creating API key
```

To create a Coinbase Exchange API key: [Coinbase Exchange → Profile → API](https://pro.coinbase.com/profile/api)
- Required permissions: **View**, **Trade**

### 3. Create config

```bash
cp config.example.json config.json
```

Edit `config.json` to set your price range around the **current market price**:

```json
{
  "campaign_name": "BTC_Grid_2026",
  "trading_pair": "BTC-USD",
  "strategy": {
    "bankroll": 1000.0,
    "grid_levels": 20,
    "grid_spacing_percent": 2.0,
    "order_size_percent": 5.0,
    "price_range": { "min": 90000, "max": 110000 },
    "scan_interval_seconds": 60
  },
  "risk_management": {
    "stop_loss_bankroll": 800.0,
    "max_open_orders": 40
  }
}
```

### 4. Setup and validate

```bash
python scripts/agent.py setup --config config.json
```

### 5. Test with dry run

```bash
python scripts/agent.py dry-run --config config.json --cycles 5
```

### 6. Start live trading

```bash
python scripts/agent.py start --config config.json
```

---

## Commands

| Command | Description |
|---------|-------------|
| `setup` | Validate config, confirm pair exists, show profit projections |
| `dry-run` | Simulate grid cycles without placing real orders |
| `start` | Start live trading (runs until stopped or stop-loss) |
| `status` | Print current P&L and position summary |
| `stop` | Cancel all open orders and export fills CSV |

---

## Configuration Reference

| Parameter | Description | Example |
|-----------|-------------|---------|
| `campaign_name` | Label for this trading session | `"BTC_Grid_2026"` |
| `trading_pair` | Coinbase product ID | `"BTC-USD"` |
| `bankroll` | Total capital to deploy (USD) | `1000.0` |
| `grid_levels` | Number of price levels | `20` |
| `grid_spacing_percent` | Spacing between levels | `2.0` |
| `order_size_percent` | Order size as % of bankroll | `5.0` |
| `price_range.min` | Lowest grid price | `90000` |
| `price_range.max` | Highest grid price | `110000` |
| `scan_interval_seconds` | Polling interval | `60` |
| `stop_loss_bankroll` | Minimum portfolio value to continue | `800.0` |
| `max_open_orders` | Hard cap on open orders | `40` |

**Tip:** Center `price_range` around the current market price. The bot uses the midpoint as its reference price for initial grid placement.

---

## Fees

Coinbase Exchange maker fee schedule (post_only limit orders):

| 30-day Volume | Maker Fee |
|---------------|-----------|
| < $10K        | 0.40%     |
| $10K–$50K     | 0.25%     |
| $50K–$100K    | 0.15%     |
| $100K+        | 0.10%     |

The bot places `post_only` limit orders to guarantee maker pricing.

---

## Log Files

All activity is logged to `logs/` as JSONL (one JSON object per line):

| File | Contents |
|------|----------|
| `grid_setup.jsonl` | Grid initialization events |
| `orders.jsonl` | All order placements and cancellations |
| `fills.jsonl` | All trade executions with fees |
| `positions.jsonl` | Position snapshots each cycle |
| `errors.jsonl` | All errors with context |

---

## Safety Features

- **Stop-loss**: Automatically stops if portfolio value drops below `stop_loss_bankroll`
- **post_only orders**: Guarantees maker fee pricing; order rejected if it would take liquidity
- **Dry-run mode**: Full simulation without real orders
- **Fill export**: CSV export of all trades on stop

---

## Known Limitations

- **No live ticker**: The `coinbase-trading` publisher does not yet expose `GET /products/{id}/ticker`. The bot uses the midpoint of `price_range` as the reference price. Request the endpoint be added via [serenorg/seren-desktop-issues](https://github.com/serenorg/seren-desktop-issues).
- **No cancel-all endpoint**: The publisher exposes `DELETE /orders/{order_id}` only. `stop` loops through active orders to cancel each individually.
- **No order history API**: Fill detection is done by comparing `active_orders` in memory against live `GET /orders`. A restart clears in-memory state; re-running `start` re-places the full grid.

---

## Troubleshooting

**`CB-ACCESS-SIGN` errors**: Ensure `CB_ACCESS_SECRET` is the raw base64-encoded secret, not URL-decoded.

**Orders rejected**: Check that your Coinbase API key has **Trade** permissions and is not IP-restricted to a different address.

**All orders on one side**: Your `price_range` doesn't bracket the current market price. Update `price_range.min`/`max` to center on current price.

---

## Support

- Issues: [serenorg/seren-desktop-issues](https://github.com/serenorg/seren-desktop-issues)
- Docs: [docs.serendb.com](https://docs.serendb.com)
