---
name: auction-trader
description: "AI agent trading skill for Sidepit's 1-second discrete auction exchange — submit orders, subscribe to market data, manage positions, and persist all auction activity to SerenDB via NNG/protobuf API."
---

# Sidepit Auction Trader

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

## When to Use

- trade on sidepit
- sidepit auction trading
- submit orders to sidepit exchange
- sidepit market data feed
- sidepit agent trading bot
- discrete auction trading
- DLOB trading

## What This Skill Does

Connects an AI agent to Sidepit's discrete-auction exchange. Sidepit runs 1-second auction cycles where all orders clear at a single price — no front-running, no adverse selection, no phantom liquidity. The auction timing matches LLM inference speed (100ms–2s), making it the only exchange where AI agents compete on intelligence, not co-location hardware.

All auction activity — orders, fills, market snapshots, and position state — is persisted to SerenDB for analysis, backtesting, and agent learning.

### Why Sidepit

| Traditional Exchange | Sidepit |
|---|---|
| HFT executes in 1–10μs (co-located silicon) | 1-second auction cycles match LLM inference |
| Orders front-run before fill | All orders submitted simultaneously |
| Phantom liquidity vanishes under volatility | Single clearing price per auction — deterministic |
| Slippage and adverse selection | No speed penalty — intelligence is the edge |

US Patent US10608825B2 — Discrete Limit Order Book (DLOB).

### Protocol Stack

| Layer | Technology |
|---|---|
| Messaging | NNG (nanomsg next gen) — Push, Sub, Req/Rep patterns |
| Serialization | Protocol Buffers (protobuf) |
| Signing | ECDSA on Bitcoin secp256k1 curve (WIF private key) |
| Transport | TCP to `api.sidepit.com` |
| Persistence | SerenDB (Postgres) via `mcp__seren__run_sql` |

## On Invoke

**Immediately run a dry-run scan without asking.** Do not present a menu of modes. Follow the workflow below to connect to the Sidepit price feed, capture market state, and simulate order placement. Display the full market snapshot and simulated orders to the user. Only after results are displayed, present available next steps (paper mode, live mode).

## Workflow Summary

1. `connect_feed` uses `connector.sidepit_exchange.subscribe_feed` — subscribe to NNG price feed on port 12122
2. `fetch_depth` uses `connector.sidepit_exchange.get_depth` — query full DLOB depth via NNG req/rep on port 12125
3. `reason` uses `transform.agent_inference` — agent reasons on order book state within 800ms budget
4. `submit_order` uses `connector.sidepit_exchange.send_order` — submit signed order via NNG push on port 12121
5. `persist_activity` uses `state.auction_activity.upsert` — persist orders, fills, snapshots to SerenDB

## API Reference

### Ports

| Protocol | Port | Pattern | Direction |
|---|---|---|---|
| Client (orders) | 12121 | Pipeline (Push) | Client → Server |
| Price Feed | 12122 | Pub/Sub | Server → Client |
| Echo (confirmations) | 12123 | Pub/Sub | Server → Client |
| Position Query | 12125 | Req/Rep | Bidirectional |

### Order Types

**NewOrder** — submit a limit order into the next auction:

| Field | Type | Description |
|---|---|---|
| `side` | int | `1` = buy, `-1` = sell |
| `size` | int | Quantity |
| `price` | int | Limit price |
| `ticker` | string | Contract symbol (e.g. `USDBTCH26`) |

**CancelOrder** — cancel a pending order by ID:

| Field | Type | Description |
|---|---|---|
| `cancel_orderid` | string | `sidepit_id:timestamp_ns` |

**AuctionBid** — bid for order priority within an auction epoch:

| Field | Type | Description |
|---|---|---|
| `epoch` | int | Auction epoch number |
| `hash` | string | Commitment hash |
| `ordering_salt` | string | Salt for ordering |
| `bid` | int | Bid amount in satoshis |

### Market Data (Price Feed)

Received on port 12122 as `MarketData` protobuf messages:

| Field | Description |
|---|---|
| `MarketQuote` | bidsize, bid, ask, asksize, last, lastsize, upordown, symbol, epoch |
| `EpochBar` | symbol, epoch, open, high, low, close, volume |
| `DepthItem[10]` | level, bid, ask, bidsize, asksize (10 levels of book depth) |

### Transaction Signing

Every order is signed with ECDSA (secp256k1):

1. Build `SignedTransaction` protobuf with `transaction` fields
2. Set `version=1`, `timestamp` in nanoseconds, `sidepit_id`
3. SHA-256 hash the serialized `transaction` bytes
4. Sign digest with WIF private key
5. Set `signature` field to hex-encoded compact signature
6. Serialize full `SignedTransaction` and send via NNG Push

## SerenDB Tables

All auction activity is persisted to SerenDB for analysis and agent learning.

- `auction_orders` — every order submitted (or simulated in dry-run)
- `auction_fills` — fill confirmations with aggressive/passive sides
- `market_snapshots` — DLOB state captured each auction cycle
- `position_snapshots` — trader position and avg price after each cycle

Schema: `sql/schema.sql`

### MCP-Native Persistence

Storage uses MCP-native SQL:

- `mcp__seren__run_sql` for reads and single writes
- `mcp__seren__run_sql_transaction` for multi-table upserts
- Project: `sidepit-auction-trader`
- Database: `sidepit_auction_trader`

## Trade Execution Contract

The words **exit**, **close**, **unwind**, **cancel**, and **stop** are immediate operator instructions. When the user issues any of these, the agent must:

1. Skip any pending pipeline steps
2. Cancel all open orders on Sidepit
3. Persist final state to SerenDB
4. Report final position state
5. Disconnect cleanly

## Pre-Trade Checklist

Before executing any live transaction the agent must:

1. Verify `SIDEPIT_ID` is set and valid
2. Verify `SIDEPIT_SECRET` (WIF private key) is set
3. Probe NNG connection to `tcp://api.sidepit.com:12121`
4. Subscribe to price feed on port 12122 and confirm data reception
5. Query active product to confirm contract is live
6. Query positions to confirm account state
7. Verify SerenDB connection and schema are initialized
8. Fail-closed if any check fails

## Dependency Validation

All dependencies are validated at startup. If any are missing the agent prints a diagnostic and exits non-zero.

- `SIDEPIT_ID` — Sidepit trader ID (ordinals address / pubkey)
- `SIDEPIT_SECRET` — WIF-encoded private key (never logged)
- `SEREN_API_KEY` — Seren gateway API key
- Python packages: `pynng`, `protobuf==3.20.1`, `secp256k1`, `base58`, `python-dotenv`, `psycopg[binary]`

## Execution Modes

- `dry-run` — simulate full pipeline, persist simulated orders to SerenDB (default)
- `live` — sign and submit real orders to Sidepit exchange

## Live Safety Opt-In

**Default mode is `dry-run`.** The agent simulates the full pipeline (subscribes to feed, reasons on state, builds orders) without sending signed transactions.

To execute live:

```bash
python scripts/agent.py --config config.json --yes-live
```

The `--yes-live` flag is the single live gate. Without it, no order is signed or submitted.

## Emergency Exit Path

```bash
python scripts/agent.py stop --config config.json --yes-live
```

This will:
1. Cancel all open orders on Sidepit
2. Persist final state to SerenDB
3. Report final positions and balances
4. Disconnect all NNG sockets

## Immediately Run

When this skill is invoked, immediately run:

```bash
python scripts/agent.py --config config.json
```

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `SIDEPIT_ID` | Yes | Your Sidepit trader ID (ordinals address / pubkey) |
| `SIDEPIT_SECRET` | Yes | WIF-encoded private key for signing orders (never logged) |
| `SEREN_API_KEY` | Yes | Seren API key — get from Seren Desktop or https://serendb.com |
| `SERENDB_URL` | No | Explicit SerenDB connection string (auto-resolved if empty) |

## API Key Setup

Before running this skill, check for an existing Seren API key in this order:

1. **Seren Desktop auth** — if the skill is running inside Seren Desktop, the runtime injects `API_KEY` automatically. Check: `echo $API_KEY`. If set, no further action is needed.
2. **Existing `.env` file** — check if `SEREN_API_KEY` is already set in the skill's `.env` file. If set, no further action is needed.
3. **Shell environment** — check if `SEREN_API_KEY` is exported in the current shell. If set, no further action is needed.

**Only if none of the above are set**, register a new agent account:

```bash
curl -sS -X POST "https://api.serendb.com/auth/agent" \
  -H "Content-Type: application/json" \
  -d '{"name":"sidepit-auction-trader"}'
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

## Installation

```bash
cd sidepit/auction-trader
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
cp config.example.json config.json
```

Clone the protobuf definitions and compile:

```bash
git submodule update --init
protoc --proto_path=proto/ --python_out=proto/ sidepit_api.proto
```

## Cost Breakdown

| Component | Estimated Cost |
|---|---|
| NNG connection | Free (direct TCP) |
| Order submission | Exchange trading fees per contract |
| Market data feed | Free (pub/sub) |
| Position queries | Free (req/rep) |
| SerenDB persistence | Included with Seren API key |

## Risks and Disclaimers

- **Market risk**: Futures and derivatives trading carries substantial risk of loss.
- **Liquidation risk**: Positions may be liquidated if margin requirements are not maintained.
- **Smart contract risk**: The DLOB protocol operates on Bitcoin L2 infrastructure.
- **Network risk**: NNG connections may drop — the agent implements reconnection logic.
- This skill does not provide financial advice. Users are responsible for their own risk management.

## Upstream References

- [Sidepit Public API](https://github.com/sidepit/Public-API) — Python client, protobuf definitions
- [Sidepit Public API Data](https://github.com/sidepit/Public-API-Data) — Protobuf schema
- [Sidepit Exchange](https://app.sidepit.com) — Live trading interface
- [Sidepit Docs](https://docs.sidepit.com) — Platform documentation
- US Patent US10608825B2 — Discrete Limit Order Book
