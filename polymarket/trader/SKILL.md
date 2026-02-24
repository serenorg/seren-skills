---
name: Polymarket Bot
display-name: Polymarket Bot
description: "Autonomous trading agent for Polymarket prediction markets using Seren ecosystem"
---

# Polymarket Trading Bot

Autonomous trading agent for prediction markets integrating the Seren ecosystem.

## ⚠️ IMPORTANT LEGAL DISCLAIMERS

**READ THIS BEFORE USING**

### Geographic Restrictions - CRITICAL
⚠️ **Polymarket is BLOCKED in the United States** following their 2022 CFTC settlement.
⚠️ **Using VPNs or other methods to circumvent geographic restrictions may violate laws**.
⚠️ **You are responsible for verifying that prediction market trading is legal in your jurisdiction**.

### Regulatory Status
- Prediction markets exist in a **regulatory gray area** in many jurisdictions
- Some governments classify them as **gambling**, others as **financial instruments**
- Some jurisdictions **prohibit them entirely**
- **Consult local laws and seek professional advice if uncertain**

### Not Financial Advice
- This bot is provided for **informational and educational purposes only**
- It does NOT constitute **financial, investment, legal, or tax advice**
- AI-generated estimates are **not guarantees and may be inaccurate**
- You are **solely responsible** for your trading decisions and any resulting gains or losses

### Risk of Loss
- Trading prediction markets involves **substantial risk of loss**
- Only risk capital you **can afford to lose completely**
- **Past performance does not indicate future results**
- Market conditions can change rapidly and unpredictably

### Tax Obligations
- Trading profits **may be subject to taxation** in your jurisdiction
- Consult a tax professional regarding your **reporting obligations**

### Age Restriction
- You must be **at least 18 years old** (or the age of majority in your jurisdiction) to use this bot

### No Warranty
- This software is provided **"as is" without warranty of any kind**
- The developers assume **no liability** for trading losses, technical failures, or regulatory consequences

---

## When to Use This Skill

Activate this skill when the user mentions:
- "trade on Polymarket"
- "set up polymarket trading"
- "start prediction market trading"
- "check my polymarket positions"
- "autonomous trading"

## For Claude: How to Invoke This Skill

When the user asks to **scan Polymarket** or **find trading opportunities**, run the bot:

### Prerequisites Check

First, verify the skill is set up:

```bash
ls ~/.config/seren/skills/polymarket-trader/.env ~/.config/seren/skills/polymarket-trader/config.json
```

If files are missing, guide user through setup (see Phase 1-2 below).

### Scanning for Opportunities (Paper Trading)

Run a single scan to find mispriced markets:

```bash
cd ~/.config/seren/skills/polymarket-trader && python3 scripts/agent.py --config config.json --dry-run --once 2>&1
```

**What this does:**

- Scans 20-50 active Polymarket markets
- Uses Perplexity to research each market
- Uses Claude to estimate fair values
- Identifies opportunities where edge > threshold
- Calculates Kelly position sizes
- **Does NOT place actual trades** (dry-run mode)
- Costs ~$1 in SerenBucks per scan

**How to present results to user:**

1. Parse output for lines starting with `✓ Opportunity found!`
2. Extract: market question, fair value, market price, edge, recommended position size
3. Summarize in a table format:

```text
Found 3 opportunities:

| Market | Fair Value | Market Price | Edge | Position |
|--------|-----------|--------------|------|----------|
| "Will BTC hit $100k?" | 67% | 54% | 13% | $15.20 |
| "Trump wins 2024?" | 48% | 55% | -7% | $8.40 SELL |
```

4. Remind user these are paper trades - no real orders placed
5. Suggest running setup if they want to enable live trading

### Checking Paper Trading History

View recent scan logs:

```bash
tail -50 ~/.config/seren/skills/polymarket-trader/logs/trading_*.log
```

### Running Live Trading (Advanced)

⚠️ **Only if user has:**

- Completed paper trading validation (50+ scans)
- $550+ budget ($500 USDC + $50 SerenBucks)
- Real Polymarket API credentials

```bash
cd ~/.config/seren/skills/polymarket-trader && python3 scripts/run_agent_server.py --config config.json &
```

Then setup cron:

```bash
python3 scripts/setup_cron.py --url http://localhost:8080/run --schedule "*/120 * * * *"
```

**Important:**

- Always confirm user has adequate budget before suggesting live mode
- Emphasize paper trading first
- Live trading requires stopping and restarting with non-dry-run flag

## Overview

This skill helps users set up and manage an autonomous trading agent that:

1. **Scans** Polymarket for active prediction markets
2. **Researches** opportunities using Perplexity AI
3. **Estimates** fair value with Claude (Anthropic)
4. **Identifies** mispriced markets (edge > threshold)
5. **Executes** trades using Kelly Criterion for position sizing
6. **Runs autonomously** on seren-cron schedule
7. **Monitors** positions and reports P&L

## Architecture

**Pure Python Implementation**
- Python agent calls Seren publishers via HTTP
- Credentials stored in `.env` file (environment variables)
- Logs written to JSONL files
- Seren-cron executes Python script on schedule

**Components:**
- `scripts/agent.py` - Main trading loop
- `scripts/seren_client.py` - Seren API client (calls publishers)
- `scripts/polymarket_client.py` - Polymarket CLOB API wrapper
- `scripts/kelly.py` - Position sizing calculator
- `scripts/position_tracker.py` - Position management
- `scripts/logger.py` - Trading logger

**Seren Publishers Used:**
- `polymarket-data` - Real-time Polymarket market data (prices, volumes, liquidity)
  - Endpoint: `GET /markets` returns active prediction markets
  - Response includes: market IDs, questions, token IDs, prices, liquidity
  - Verified working with 100+ markets returned

- `polymarket-trading-serenai` - Polymarket CLOB trading API
  - Place/cancel orders with server-side EIP-712 signing
  - Query positions, open orders, balances
  - Requires Polymarket L2 credentials (API key, passphrase, secret, address)

- `perplexity` - Perplexity AI research (via OpenRouter)
  - Model: `sonar` for fast research
  - Returns AI-generated summaries with citations
  - Used to research market questions before trading

- `seren-models` - Multi-model LLM inference (via OpenRouter)
  - 200+ models available (Claude, GPT, Gemini, Llama, etc.)
  - Used model: `anthropic/claude-sonnet-4.5`
  - Estimates fair value probabilities from research

- `seren-cron` - Autonomous job scheduling
  - Schedule Python agent to run on cron expressions
  - Executes scan cycles automatically (e.g., every 10 minutes)
  - Pause/resume/delete jobs programmatically

---

## Setup Workflow

### Phase 1: Install Dependencies

Check Python version and install requirements:

```bash
cd skills/polymarket-trader

# Check Python version (need 3.9+)
python3 --version

# Install dependencies
pip3 install -r requirements.txt
```

### Phase 2: Configure Credentials

Create `.env` file from template:

```bash
cp .env.example .env
```

Edit `.env` and add your credentials:

```bash
# Seren API key - get from https://app.serendb.com/settings/api-keys
SEREN_API_KEY=your_seren_api_key_here

# Polymarket credentials - get from https://polymarket.com
# (Settings > API Keys > Derive API Key)
POLY_API_KEY=your_polymarket_api_key_here
POLY_PASSPHRASE=your_polymarket_passphrase_here
POLY_SECRET=your_polymarket_secret_here
POLY_ADDRESS=your_wallet_address_here
```

**How to get Polymarket credentials:**
1. Visit [polymarket.com](https://polymarket.com)
2. Connect your wallet
3. Navigate to Settings > API Keys
4. Click "Derive API Key"
5. Save your credentials securely

**Security Note:**
- Never commit `.env` to git (already in `.gitignore`)
- Keep credentials secure
- Credentials grant access to your Polymarket wallet

### Phase 3: Configure Risk Parameters

Copy the example config and customize:

```bash
cp config.example.json config.json
```

Edit `config.json` to set your risk parameters:

```json
{
  "bankroll": 100.0,
  "mispricing_threshold": 0.08,
  "max_kelly_fraction": 0.06,
  "scan_interval_minutes": 10,
  "max_positions": 10,
  "stop_loss_bankroll": 0.0
}
```

**Parameter Guide:**

#### bankroll
Total capital available for trading (in USDC).
- Testing: $50-100
- Serious: $500+
- **Only risk what you can afford to lose**

#### mispricing_threshold
Minimum edge required to trade (as decimal, e.g., 0.08 = 8%).
- 0.05: Aggressive (more trades, smaller edges)
- 0.08: Balanced (recommended)
- 0.12: Conservative (fewer trades, larger edges)

#### max_kelly_fraction
Maximum % of bankroll per trade (as decimal, e.g., 0.06 = 6%).
- 0.03: Very conservative
- 0.06: Balanced (recommended)
- 0.10: Aggressive (higher variance)

#### scan_interval_minutes
How often to scan for opportunities.
- 5 minutes: High frequency (~$5-10/day in API costs)
- 10 minutes: Balanced (~$2-5/day)
- 30 minutes: Conservative (~$0.50-1.50/day)

#### max_positions
Maximum concurrent open positions.
- Small bankroll (<$100): 5-10
- Medium bankroll ($100-500): 10-20
- Large bankroll (>$500): 20-50

#### stop_loss_bankroll
Stop trading if bankroll drops to this amount.
- 0: Stop only if completely depleted
- 50% of initial: Stop if down 50%

### Phase 4: Fund Your Wallets

⚠️ **REALITY CHECK: The Economics of Automated Trading**

**You need at least $550 total to trade profitably with this bot.**

This is not a recommendation - it's math. Here's why:

#### The Problem with Small Bankrolls

The bot costs ~$12/day to run (at 2-hour scan intervals). With a $20 bankroll:

- Max position size: 3% × $20 = **$0.60 per trade**
- To break even in 3 days: need **224% return** ($45 profit from $20 capital)
- Reality: Even the best trades return 10-30%, giving you $0.06-0.18 profit per position
- **You're spending $12/day to make $0.50/day**

This is like hiring a $100/hour analyst to trade a $10 account. The math doesn't work.

#### Minimum Viable Budget: $550

To have a realistic chance of offsetting API costs and achieving profitability:

| Item             | Amount   | Purpose                                       |
| ---------------- | -------- | --------------------------------------------- |
| **Polygon USDC** | $500     | Trading capital (allows $15-30 positions)     |
| **SerenBucks**   | $50      | API costs (4+ days of operation)              |
| **Total**        | **$550** | Minimum to trade with positive expected value |

With $500 bankroll:

- Position sizes: $15-30 each (at 3-6% Kelly sizing)
- Plausible profit over 4 days: $50-100 (10-20% return on multiple trades)
- API cost: -$48
- **Net: +$2-52 profit (break-even to profitable)**

---

#### Budget Tiers

##### 🔴 Below Minimum (<$550 total)

- **Status**: 🚨 **WILL LOSE MONEY**
- **Reality**: Trading profits cannot offset API costs with small positions
- **Use case**: Educational only - learning how the system works
- **Expected outcome**: Net loss of ~$40-50 after SerenBucks depleted

##### 🟢 Minimum Viable ($550-800 total)

- **SerenBucks**: $50-100
- **Polygon USDC**: $500
- **Scan interval**: 120 minutes (2 hours)
- **Daily API cost**: $12
- **Expected outcome**: Break-even to modest profit
- **Best for**: First serious attempt at profitable automated trading

##### 🟡 Active Trader ($800-1,500 total)

- **SerenBucks**: $100-200
- **Polygon USDC**: $700-1,300
- **Scan interval**: 60 minutes (1 hour)
- **Daily API cost**: $24
- **Expected outcome**: Profitable if edge is real
- **Best for**: Experienced traders scaling up

##### 🔵 Serious Trader ($1,500+ total)

- **SerenBucks**: $200+
- **Polygon USDC**: $1,300+
- **Scan interval**: 30 minutes
- **Daily API cost**: $48
- **Expected outcome**: Maximum opportunity capture
- **Best for**: High conviction in strategy, willing to scale

---

#### Paper Trading (RECOMMENDED FOR EVERYONE)

**Before deploying real capital, paper trade for 1-2 weeks to validate your edge.**

Paper trading uses real market data and real AI analysis, but simulates trades instead of placing actual orders. This lets you:

- Validate the strategy actually finds mispriced markets
- Measure real win rate and average edge
- Test different risk parameters (Kelly fraction, mispricing threshold)
- Build confidence before risking capital

**How to paper trade:**

```bash
python3 scripts/agent.py --config config.json --dry-run
```

**What happens in dry-run mode:**

- ✅ Scans real Polymarket markets
- ✅ Researches opportunities with Perplexity (real API calls)
- ✅ Estimates fair values with Claude (real API calls)
- ✅ Calculates Kelly position sizes
- ✅ Logs paper trades to `logs/trading_*.log`
- ❌ Does NOT place actual Polymarket orders
- ❌ Does NOT require Polymarket API credentials

**Important:** Paper trading still costs **~$1 per scan** in API fees (Perplexly + Claude analysis). With $50 SerenBucks, you can run 50 paper trades over 1-2 weeks to validate the strategy.

**Recommended paper trading plan:**

1. Fund $50 SerenBucks (covers ~50 scans)
2. Set `"scan_interval_minutes": 120` in config.json (2-hour intervals)
3. Run paper trading for 5-7 days (60-84 scans)
4. Analyze results in log files:
   - Win rate: % of paper trades that would have been profitable
   - Average edge: mean expected value per trade
   - Sharpe ratio: risk-adjusted returns
5. If paper trading shows consistent edge, move to live trading with $550+ budget

**Manual paper trading (if <$50 SerenBucks):**

Run scans one at a time when you want:

```bash
python3 scripts/agent.py --config config.json --dry-run --once
```

- You control when to spend API credits ($1 per scan)
- With $20 SerenBucks: run 20 scans over weeks/months
- Scout for opportunities at your own pace
- No autonomous scheduling

---

#### Moving from Paper to Live Trading

Once paper trading validates your edge (recommended: 50+ scans with positive expected value), you're ready for live trading.

**Requirements for live trading:**

- ✅ Successful paper trading period (1-2 weeks, 50+ scans)
- ✅ Minimum $550 budget ($500 USDC + $50 SerenBucks)
- ✅ Polymarket API credentials (see Phase 2)
- ✅ Understanding of Kelly Criterion risk management

**If you don't have $550 yet:**

- Continue paper trading to refine strategy
- Save up capital while accumulating paper trade data
- Use smaller scan intervals once you have budget

---

#### How to Fund SerenBucks

1. Visit: https://app.serendb.com/wallet/deposit
2. Choose deposit method:
   - Credit card (instant)
   - Crypto transfer (USDC, ETH, BTC)
3. Minimum recommended: $50 for uninterrupted operation

**Cost breakdown per scan cycle** (~$1.00 total):
- Market data fetch: $0.10
- Perplexity research: $0.01 × 20 markets = $0.20
- Claude fair value estimation: $0.035 × 20 markets = $0.70

**Daily costs by scan interval:**
- 30 min intervals: 48 scans/day × $1.00 = $48/day
- 60 min intervals: 24 scans/day × $1.00 = $24/day
- 120 min intervals: 12 scans/day × $1.00 = $12/day

---

#### How to Fund Polymarket (Polygon USDC)

1. **Bridge USDC to Polygon PoS** using:
   - [Polygon Bridge](https://wallet.polygon.technology/bridge) (official)
   - [Hop Exchange](https://app.hop.exchange/) (faster)
   - [Connext](https://bridge.connext.network/) (alternative)

2. **Send to your Polymarket wallet address**:
   - Find your address: https://polymarket.com/wallet
   - Send bridged USDC to this address
   - Wait for confirmation (~2-5 minutes)

3. **Verify balance**:
   - Check on Polymarket: https://polymarket.com/wallet
   - Or check on PolygonScan: https://polygonscan.com/address/YOUR_ADDRESS

**Trading capital recommendations:**
- Minimum: $20 (allows small positions for testing)
- Conservative: $50-100 (better position sizing)
- Serious: $500+ (optimal Kelly Criterion sizing)

---

#### Checking Your Balances

Before running the bot, verify both balances:

**Check SerenBucks:**
```bash
# If you have Seren MCP connected
# The bot will display balance when it starts
```

**Check Polymarket USDC:**
- Visit: https://polymarket.com/wallet
- Or the bot will show balance at startup

### Phase 5: Dry-Run Test (STRONGLY RECOMMENDED)

Test the bot without placing real trades:

```bash
python3 scripts/agent.py --config config.json --dry-run
```

**Dry-run mode:**
- ✅ Scans markets (when implemented)
- ✅ Researches opportunities using Perplexity
- ✅ Estimates fair values using Claude
- ✅ Calculates position sizes using Kelly Criterion
- ✅ Logs everything to files
- ❌ Does NOT place actual trades

**Expected output:**
```
============================================================
🔍 Polymarket Scan Starting - 2026-02-12 14:35:00 UTC
============================================================

Balances:
  SerenBucks: $23.45
  Polymarket: $100.00

Scanning markets...
  Found 23 markets

Evaluating: "Will BTC hit $100k by March 2026?"
  Current price: 54.0%
  🧠 Researching: "Will BTC hit $100k by March 2026?"
  💡 Estimating fair value...
     Fair value: 67.0% (confidence: medium)
    ✓ Opportunity found!
      Edge: 13.0%
      Side: BUY
      Size: $3.24 (5.4% of available)
      Expected value: +$0.42

    [DRY-RUN] Would place BUY order:
      Market: "Will BTC hit $100k by March 2026?"
      Size: $3.24
      Price: 54.0%
      Expected value: +$0.42

============================================================
Scan complete!
  Markets scanned: 23
  Opportunities: 8
  Trades executed: 0 (dry-run)
  Capital deployed: $0.00
  API cost: ~$0.46 SerenBucks
============================================================
```

### Phase 6: Live Trading Confirmation

⚠️ **CRITICAL - You must explicitly confirm before enabling live trading**

**Before going live, ask yourself:**
1. Have I tested in dry-run mode?
2. Do I understand the risks?
3. Can I afford to lose this capital?
4. Is prediction market trading legal in my jurisdiction?
5. Have I funded both SerenBucks and Polymarket?

**Display this warning:**

```
⚠️  LIVE TRADING CONFIRMATION

You're about to enable LIVE TRADING with real money.

Configuration:
  • Bankroll: $100.00
  • Max per trade: 6% ($6.00)
  • Scan interval: Every 10 minutes
  • Stop loss: $0.00 (stop when depleted)
  • Max positions: 10

Estimated Costs:
  • SerenBucks: ~$2-5 per day (for API calls)
  • Trading capital: Up to $100.00 (your bankroll)

Risks:
  ⚠️  You can lose money - prediction markets are uncertain
  ⚠️  Only risk what you can afford to lose
  ⚠️  Past performance doesn't guarantee future results
  ⚠️  The agent makes autonomous decisions based on AI estimates
  ⚠️  Market conditions can change rapidly
  ⚠️  Slippage and fees may reduce returns

The agent will run on schedule until you stop it.

Type exactly: START LIVE TRADING
(or 'cancel' to abort)
```

**Wait for EXACT confirmation.** Do not proceed unless user types "START LIVE TRADING".

### Phase 7: Run Live

Once confirmed, run the agent:

```bash
# Run once
python3 scripts/agent.py --config config.json

# Or set up with seren-cron for autonomous operation
```

**Setting up seren-cron** (for autonomous scheduling):

```python
from seren_client import SerenClient

seren = SerenClient()

# Create cron job
job = seren.create_cron_job(
    name='polymarket-trader',
    schedule='*/10 * * * *',  # Every 10 minutes
    url='http://localhost:8000/run-scan',  # Your endpoint
    method='POST',
    headers={
        'Authorization': 'Bearer YOUR_WEBHOOK_TOKEN'
    }
)

print(f"Cron job created: {job['id']}")
```

**Note:** You'll need to set up a web endpoint that calls `scripts/agent.py` when triggered.

---

## Control Commands

### Show Status

Read current positions and display status:

```python
import json

# Read positions
with open('skills/polymarket-trader/logs/positions.json', 'r') as f:
    data = json.load(f)

# Display
print("📊 Polymarket Trading Status\n")
print(f"Positions: {data['position_count']}")
print(f"Total unrealized P&L: ${data['total_unrealized_pnl']:.2f}")

for pos in data['positions']:
    pnl_symbol = '+' if pos['unrealized_pnl'] >= 0 else ''
    print(f"\n  {pos['market']}")
    print(f"  {pos['side']} ${pos['size']:.2f} @ {pos['entry_price'] * 100:.1f}%")
    print(f"  Now: {pos['current_price'] * 100:.1f}% ({pnl_symbol}${pos['unrealized_pnl']:.2f})")
```

### Show Recent Trades

Read and display trade history:

```python
import json

# Read last 20 trades
with open('skills/polymarket-trader/logs/trades.jsonl', 'r') as f:
    lines = f.readlines()

trades = [json.loads(line) for line in lines[-20:]]

print("📝 Recent Trades (Last 20)\n")

for i, trade in enumerate(reversed(trades), 1):
    pnl_symbol = '' if trade['pnl'] is None else ('+' if trade['pnl'] >= 0 else '')
    status_emoji = '🟢' if trade['status'] == 'open' else '✓' if trade['pnl'] and trade['pnl'] > 0 else '✗'

    print(f"{i}. {status_emoji} {trade['side']} ${trade['size']:.2f} @ {trade['price'] * 100:.1f}%")
    print(f"   \"{trade['market']}\"")
    if trade['pnl'] is not None:
        print(f"   P&L: {pnl_symbol}${trade['pnl']:.2f}")
    print()
```

### Pause/Resume Trading

**Pause** (stop scanning, keep positions):
```python
seren = SerenClient()
config = json.load(open('config.json'))

seren.pause_cron_job(config['cron_job_id'])
print("⏸️  Trading paused")
```

**Resume**:
```python
seren.resume_cron_job(config['cron_job_id'])
print("▶️  Trading resumed")
```

### Stop Trading

**Stop completely** (cancel cron job):
```python
seren.delete_cron_job(config['cron_job_id'])
print("🛑 Trading stopped")
```

---

## Monitoring & Logs

All activity is logged to JSONL files in `logs/`:

### trades.jsonl
One line per trade (opened or closed):

```json
{"timestamp": "2026-02-12T14:35:00Z", "market": "Will BTC hit $100k by March?", "market_id": "0x123...", "side": "BUY", "size": 3.24, "price": 0.54, "fair_value": 0.67, "edge": 0.13, "status": "open", "pnl": null}
```

### scan_results.jsonl
One line per scan cycle:

```json
{"timestamp": "2026-02-12T14:35:00Z", "dry_run": false, "markets_scanned": 500, "opportunities_found": 23, "trades_executed": 3, "capital_deployed": 9.45, "api_cost": 1.12, "serenbucks_balance": 48.88, "polymarket_balance": 103.45}
```

### positions.json
Current state (updated after each trade):

```json
{
  "positions": [
    {
      "market": "Will BTC hit $100k by March?",
      "market_id": "0x123...",
      "token_id": "0x456...",
      "side": "BUY",
      "entry_price": 0.54,
      "current_price": 0.58,
      "size": 3.24,
      "unrealized_pnl": 0.84,
      "opened_at": "2026-02-12T14:35:00Z"
    }
  ],
  "total_unrealized_pnl": 0.84,
  "position_count": 1,
  "last_updated": "2026-02-12T18:00:00Z"
}
```

### notifications.jsonl
Critical events for user notification:

```json
{"timestamp": "2026-02-12T15:00:00Z", "level": "warning", "title": "Low SerenBucks Balance", "message": "Current: $1.23, Recommended: $20.00"}
```

---

## How It Works (Technical Details)

### Fair Value Estimation

The bot uses Claude to estimate true probabilities:

```python
def estimate_fair_value(market_question, current_price, research):
    prompt = f"""You are an expert analyst estimating the true probability of prediction market outcomes.

Market Question: {market_question}

Current Market Price: {current_price * 100:.1f}%

Research Summary:
{research}

Based on the research and your analysis, estimate the TRUE probability of this outcome occurring.

Provide your response in this exact format:
PROBABILITY: [number between 0 and 100]
CONFIDENCE: [low, medium, or high]
REASONING: [brief explanation]"""

    # Call Claude via seren-models
    response = seren.call_publisher(
        publisher='seren-models',
        method='POST',
        path='/chat/completions',
        body={
            'model': 'anthropic/claude-sonnet-4-20250514',
            'messages': [{'role': 'user', 'content': prompt}],
            'temperature': 0.3
        }
    )

    # Parse and return
    # (parsing logic extracts PROBABILITY and CONFIDENCE from response)
```

### Position Sizing (Kelly Criterion)

```python
def calculate_position_size(fair_value, market_price, bankroll, max_kelly=0.06):
    """
    Calculate optimal position size using Kelly Criterion

    Formula: kelly = (fair_value - price) / (1 - price) for BUY
    Uses quarter-Kelly (divide by 4) for conservatism
    Caps at max_kelly of bankroll
    """
    kelly = (fair_value - market_price) / (1 - market_price)
    kelly_adjusted = kelly / 4  # Quarter-Kelly
    kelly_capped = min(kelly_adjusted, max_kelly)

    position_size = bankroll * kelly_capped
    return round(position_size, 2)
```

---

## Implementation Status

### ✅ Fully Implemented & Working

**Core Trading Engine:**
- ✅ Market scanning via `polymarket-data` publisher (100+ active markets)
- ✅ AI research via `perplexity` publisher (Perplexity AI integration)
- ✅ Fair value estimation via `seren-models` publisher (Claude Sonnet 4.5)
- ✅ Kelly Criterion position sizing
- ✅ Order placement via `polymarket-trading-serenai` publisher (server-side EIP-712 signing)
- ✅ Position tracking with unrealized P&L calculation
- ✅ Comprehensive JSONL logging (trades, scans, positions)

**Infrastructure:**
- ✅ Seren API client with publisher routing
- ✅ Environment variable credential management
- ✅ Dry-run mode (simulation without placing trades)
- ✅ Configuration system (JSON-based risk parameters)

**Seren Publishers Used:**
- `polymarket-data` - Real-time market data (prices, liquidity, volumes)
- `polymarket-trading-serenai` - Order placement with server-side signing
- `perplexity` - AI-powered market research
- `seren-models` - LLM inference (Claude, GPT, Gemini, etc.)
- `seren-cron` - Autonomous job scheduling

### ⚠️ Limitations

**Not Automated (Manual Only):**
- Position closing/exit strategy (must close manually on Polymarket)
- Bankroll rebalancing after profits/losses

**Not Implemented:**
- Web dashboard (command-line only)
- Email/webhook notifications (file logs only)
- Backtesting with historical data
- Real-time Polymarket balance checking (placeholder returns $0.00)

---

## Cost Estimation

### SerenBucks (API Calls)
Per scan cycle:
- Perplexity research: $0.01 × markets researched
- Claude fair value: $0.01 × markets evaluated
- Total: ~$0.50-2.00 per scan (depends on markets scanned)

**Daily costs:**
- Every 5 min: $5-10/day
- Every 10 min: $2-5/day
- Every 30 min: $0.50-1.50/day

### Polymarket Trading
- Order placement: $0.005 per order
- Order cancellation: $0.002 per cancellation
- Price queries: $0.001 per request

---

## Troubleshooting

### "SEREN_API_KEY is required"
- Create `.env` file from `.env.example`
- Add your Seren API key

### "Polymarket credentials required"
- Add `POLY_API_KEY`, `POLY_PASSPHRASE`, `POLY_ADDRESS` to `.env`

### "Low SerenBucks balance"
- Deposit at: https://app.serendb.com/wallet/deposit
- Maintain at least $20 for smooth operation

### "Publisher call failed: 401"
- Check your API keys are correct
- Verify credentials haven't expired

---

## Best Practices

### For Users

1. **Start small**: Test with $50-100 before scaling up
2. **Use dry-run first**: Always test before going live
3. **Monitor regularly**: Check logs and positions daily
4. **Adjust conservatively**: Increase bankroll gradually
5. **Understand the risks**: Only trade what you can afford to lose
6. **Keep funded**: Maintain sufficient SerenBucks balance

### For Developers

1. **Always validate inputs**: Check config parameters are in valid ranges
2. **Never skip confirmation**: Live trading requires explicit user consent
3. **Log everything**: All trades, scans, errors go to log files
4. **Handle errors gracefully**: Never crash - log and notify
5. **Protect credentials**: Use environment variables, never log secrets
6. **Estimate costs proactively**: Warn users about SerenBucks costs

---

## AgentSkills.io Standard

This skill follows the [AgentSkills.io](https://agentskills.io) open standard for agent skills, ensuring compatibility across:
- Claude Code
- OpenAI Codex
- Google Gemini
- Any compatible LLM tool

Taariq Lewis, SerenAI, Paloma, and Volume at https://serendb.com
Email: hello@serendb.com
