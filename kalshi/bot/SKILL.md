---
name: kalshi-bot
display-name: Kalshi Trading Bot
description: "Autonomous trading agent for Kalshi prediction markets using Seren ecosystem"
---

# Kalshi Trading Bot

Autonomous trading agent for Kalshi prediction markets integrating the Seren ecosystem.

## IMPORTANT LEGAL DISCLAIMERS

**READ THIS BEFORE USING**

### Regulatory Status
- Kalshi is a CFTC-regulated exchange for event contracts in the United States
- Trading on Kalshi is subject to US federal regulations
- Some event contracts may have additional restrictions
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
- Kalshi issues 1099 forms for US taxpayers
- Consult a tax professional regarding your **reporting obligations**

### Age Restriction
- You must be **at least 18 years old** (or the age of majority in your jurisdiction)

### No Warranty
- This software is provided **"as is" without warranty of any kind**
- The developers assume **no liability** for trading losses, technical failures, or regulatory consequences

---

## When to Use This Skill

Activate this skill when the user mentions:
- "trade on Kalshi"
- "set up Kalshi trading"
- "Kalshi prediction markets"
- "check my Kalshi positions"
- "autonomous Kalshi trading"

## For Claude: How to Invoke This Skill

**Immediately run a dry-run scan without asking.** Do not present a menu or ask the user to choose between scan/trade/setup. Execute the paper scan by default. Only after results are displayed, present available next steps (live trading setup, position management). If the user explicitly requests a specific action in their invocation message, run that action instead.

When the user asks to **scan Kalshi** or **find trading opportunities**, run the bot:

### Prerequisites Check

First, verify the skill is set up:

```bash
ls ~/.config/seren/skills/kalshi-bot/.env ~/.config/seren/skills/kalshi-bot/config.json
```

If files are missing, guide user through setup (see Phase 1-2 below).

### Scanning for Opportunities (Paper Trading)

Run a single scan to find mispriced markets:

```bash
cd ~/.config/seren/skills/kalshi-bot && python3 scripts/agent.py --config config.json --dry-run 2>&1
```

**What this does:**

- Scans up to 200 active Kalshi markets
- Filters by volume (>$5K), open interest (>100), and time to resolution
- Uses Perplexity to research top candidates
- Uses Claude to estimate fair values
- Identifies opportunities where edge > 8% threshold
- Calculates Kelly position sizes (quarter-Kelly, capped at 6%)
- **Does NOT place actual trades** (dry-run mode)
- Costs ~$1 in SerenBucks per scan

**How to present results to user:**

1. Parse the JSON output at the end of stdout
2. Extract opportunities array with: ticker, question, side, price_cents, fair_value, edge, expected_value
3. Summarize in a table:

```text
Found 3 opportunities:

| Market | Side | Price | Fair Value | Edge | Contracts | EV |
|--------|------|-------|------------|------|-----------|----|
| KXBTC-25APR-T100K | YES | 54c | 67% | 13% | 11 | $1.43 |
| KXFED-25MAY-RATE | NO | 40c | 28% | 12% | 8 | $0.96 |
```

4. Remind user these are paper trades -- no real orders placed
5. Suggest running setup if they want to enable live trading

### Running Live Trading (Advanced)

Only if user has:
- Completed paper trading validation
- $100+ budget on Kalshi
- Real Kalshi API credentials (API key + RSA private key)

```bash
cd ~/.config/seren/skills/kalshi-bot && python3 scripts/agent.py --config config.json --yes-live --once
```

### Setting Up Automated Scans via seren-cron

```bash
cd ~/.config/seren/skills/kalshi-bot && python3 scripts/setup_cron.py create --config config.json --schedule "0 */2 * * *" --dry-run
```

Then start the local pull runner:

```bash
python3 scripts/run_local_pull_runner.py --config config.json
```

## Trade Execution Contract

When the user gives a direct exit instruction (`sell`, `close`, `exit`, `unwind`, `flatten`), execute the exit path immediately.
Do not editorialize or argue against recovering remaining funds.
If the user request is ambiguous, ask only the minimum clarifying question needed to identify the positions to exit.

## Kalshi Order Execution Rules

- Use the Kalshi REST API directly via `KalshiClient` in `scripts/kalshi_client.py`
- All prices are in **CENTS** (1-99). Contracts pay **$1.00** if correct, **$0.00** if wrong
- For immediate sells, place a limit order at the best bid price
- RSA-PSS signing is required for all authenticated endpoints
- Auth headers: `KALSHI-ACCESS-KEY`, `KALSHI-ACCESS-SIGNATURE`, `KALSHI-ACCESS-TIMESTAMP`

## Pre-Trade Checklist (Mandatory)

Before any live buy, sell, or unwind:

1. Fetch the live orderbook for the market ticker
2. Verify spread is acceptable (edge/spread ratio > 3x)
3. Verify Kalshi API credentials are configured and authentication works
4. Check portfolio balance has sufficient funds
5. If any check fails, fail closed with a concrete remediation message

## Live Safety Opt-In

Default mode is `--dry-run`. Live trading requires:

- `python scripts/agent.py --config config.json --yes-live`
- or a seren-cron job created with `--live` flag

The `--yes-live` flag is a startup-only live opt-in. It is not a per-order approval prompt.

## Overview

This skill helps users set up and manage an autonomous trading agent that:

1. **Scans** Kalshi for active prediction markets
2. **Researches** opportunities using Perplexity AI
3. **Estimates** fair value with Claude (Anthropic)
4. **Identifies** mispriced markets (edge > threshold)
5. **Executes** trades using Kelly Criterion for position sizing
6. **Runs autonomously** on seren-cron schedule
7. **Monitors** positions and reports P&L

## Architecture

**Pure Python Implementation**
- Python agent calls Seren publishers via HTTP for research and LLM inference
- Kalshi REST API called directly with RSA-PSS request signing
- No third-party trading SDKs required
- Logs written to JSONL files
- Seren-cron stores the schedule, skill-local runner executes `scripts/agent.py` locally

**Components:**
- `scripts/agent.py` - Main trading loop (three-stage pipeline)
- `scripts/seren_client.py` - Seren API client (calls publishers)
- `scripts/kalshi_client.py` - Kalshi REST API client with RSA signing
- `scripts/setup_cron.py` - seren-cron local-pull schedule management
- `scripts/run_local_pull_runner.py` - Local seren-cron polling runner
- `scripts/kelly.py` - Kelly Criterion position sizing
- `scripts/position_tracker.py` - Position and P&L management
- `scripts/logger.py` - Trading logger (JSONL)
- `scripts/risk_guards.py` - Drawdown, aging, and auto-pause guards

**Seren Publishers Used:**
- `perplexity` - Perplexity AI research (via OpenRouter)
  - Model: `sonar` for fast research
  - Returns AI-generated summaries with citations
  - Used to research market questions before trading

- `seren-models` - Multi-model LLM inference (via OpenRouter)
  - 200+ models available (Claude, GPT, Gemini, Llama, etc.)
  - Used model: `anthropic/claude-sonnet-4.5`
  - Estimates fair value probabilities from research

- `seren-cron` - Autonomous local-pull scheduling
  - Stores runner registration and cron schedules in Seren
  - Lets a skill-local polling process claim due work and run `scripts/agent.py` locally
  - Pause/resume/delete jobs and runners programmatically

**Kalshi REST API (direct):**
- Base URL: `https://api.elections.kalshi.com/trade-api/v2`
- Auth: RSA-PSS key signing per request
- Markets: `GET /markets`, `GET /markets/{ticker}`, `GET /markets/{ticker}/orderbook`
- Events: `GET /events`, `GET /events/{event_ticker}`
- Orders: `POST /portfolio/orders`, `DELETE /portfolio/orders/{order_id}`
- Positions: `GET /portfolio/positions`, `GET /portfolio/balance`
- Fills: `GET /portfolio/fills`

---

## API Key Setup

Before running this skill, check for an existing Seren API key in this order:

1. **Seren Desktop auth** -- if running inside Seren Desktop, the runtime injects `API_KEY` automatically. Check: `echo $API_KEY`. If set, no further action needed.
2. **Existing `.env` file** -- check if `SEREN_API_KEY` is already set. If set, no further action needed.
3. **Shell environment** -- check if `SEREN_API_KEY` is exported. If set, no further action needed.

**Only if none of the above are set**, register a new agent account:

```bash
curl -sS -X POST "https://api.serendb.com/auth/agent" \
  -H "Content-Type: application/json" \
  -d '{"name":"kalshi-bot"}'
```

Extract the API key from `.data.agent.api_key` -- **shown only once**. Write it to `.env`:

```env
SEREN_API_KEY=<the-returned-key>
```

**Do not create a new account if a key already exists.** Creating a duplicate results in a $0-balance key.

## Setup Workflow

### Phase 1: Install Dependencies

```bash
cd kalshi/bot

# Check Python version (need 3.10+)
python3 --version

# Install dependencies
pip3 install -r requirements.txt
```

### Phase 2: Configure Credentials

```bash
cp .env.example .env
```

Edit `.env`:

```bash
# Seren API key
SEREN_API_KEY=your_seren_api_key_here

# Kalshi API credentials (from https://kalshi.com/account/api)
KALSHI_API_KEY=your_kalshi_api_key_here
KALSHI_PRIVATE_KEY_PATH=/path/to/kalshi_private_key.pem
```

**How to get Kalshi API credentials:**
1. Log into [kalshi.com](https://kalshi.com)
2. Navigate to Account > API Keys
3. Generate an API key and RSA key pair
4. Save the private key PEM file securely
5. Set `KALSHI_API_KEY` and `KALSHI_PRIVATE_KEY_PATH` in `.env`

**Security Note:**
- Never commit `.env` to git
- Keep RSA private key file secure (600 permissions)
- Credentials grant trading access to your Kalshi account

### Phase 3: Configure Risk Parameters

```bash
cp config.example.json config.json
```

Edit `config.json`:

```json
{
  "bankroll": 100.0,
  "mispricing_threshold": 0.08,
  "max_kelly_fraction": 0.06,
  "max_positions": 10
}
```

**Parameter Guide:**

#### bankroll
Total capital available for trading (in USD).
- Testing: $50-100
- Small: $100-500
- Medium: $500-2000

#### mispricing_threshold
Minimum edge required to trade (as decimal).
- Conservative: 0.10 (10%)
- Default: 0.08 (8%)
- Aggressive: 0.05 (5%)

#### max_kelly_fraction
Maximum percentage of bankroll per trade.
- Conservative: 0.03 (3%)
- Default: 0.06 (6%)
- Aggressive: 0.10 (10%)

Quarter-Kelly is applied automatically (raw Kelly / 4).

## Usage

### Paper Trading (Recommended Start)

```bash
python3 scripts/agent.py --config config.json --dry-run
```

### Live Trading (After Validation)

```bash
python3 scripts/agent.py --config config.json --yes-live --once
```

### Automated Scheduling

Create a cron schedule:
```bash
python3 scripts/setup_cron.py create --schedule "0 */2 * * *" --dry-run
```

Start the local runner:
```bash
python3 scripts/run_local_pull_runner.py --config config.json
```

Manage schedules:
```bash
python3 scripts/setup_cron.py list
python3 scripts/setup_cron.py pause --job-id <id>
python3 scripts/setup_cron.py resume --job-id <id>
python3 scripts/setup_cron.py delete --job-id <id>
```

## Risk Management

### Position Limits
- Maximum 10 simultaneous positions (configurable)
- Maximum 3 positions per event (diversification)
- Quarter-Kelly sizing (conservative)
- 6% max bankroll per trade

### Safety Guards
- **Drawdown detection**: Alert when portfolio drops > 15%
- **Position aging**: Flag positions older than 72 hours
- **Auto-pause**: Pause cron job when SerenBucks balance is low
- **Spread check**: Reject trades where edge/spread ratio < 3x
- **Extreme divergence**: Reject when model disagrees > 50% with market

### Kalshi-Specific Protections
- All prices verified in cents (1-99 range)
- Contract count validated before submission
- RSA signature verified for every authenticated request
- Dry-run is the default mode

## Troubleshooting

### "Seren API key is required"
Set `SEREN_API_KEY` in `.env` or ensure `API_KEY` is injected by Seren Desktop.

### "Kalshi private key not configured"
Set `KALSHI_PRIVATE_KEY_PATH` in `.env` pointing to your RSA private key PEM file.

### "Kalshi API error: 401"
API key or RSA signature is invalid. Re-generate credentials at kalshi.com/account/api.

### "No markets found"
Kalshi API may be down, or all markets are filtered out. Check your `min_volume` and `min_open_interest` settings.

### Low SerenBucks balance
Each scan costs ~$1 in SerenBucks (Perplexity research + Claude inference). Top up at console.serendb.com.

## FAQ

**Q: How much does it cost to run?**
A: ~$1 in SerenBucks per scan cycle (Perplexity + Claude calls). Scans every 2 hours = ~$12/day.

**Q: What is the minimum bankroll?**
A: $50 recommended for paper trading. $100+ for live trading.

**Q: How does pricing work on Kalshi?**
A: Prices are in CENTS (1-99). A YES contract at 54c costs $0.54 and pays $1.00 if the event occurs. A NO contract at 46c (100 - 54) costs $0.46 and pays $1.00 if the event does not occur.

**Q: Is this legal?**
A: Kalshi is a CFTC-regulated exchange. Check your local regulations regarding event contracts.

**Q: Can I lose more than my bankroll?**
A: No. Kalshi contracts have limited risk. Maximum loss per contract is the purchase price.
