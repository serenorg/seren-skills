---
name: grid-trader
description: "Automated grid trading bot for Coinbase Exchange — profits from price oscillation using a mechanical, non-directional strategy"
---

# Coinbase Grid Trader

Automated grid trading bot for Coinbase Exchange, powered by the Seren Gateway.

## What This Skill Provides

- Automated Coinbase Exchange grid trading with dry-run and live modes
- Price-range based grid generation with risk controls
- JSONL logs for setup, orders, fills, positions, and errors
- MCP-native SerenDB persistence for sessions, events, orders, fills, and position snapshots

## What is Grid Trading?

Grid trading places a ladder of buy orders below the market price and sell orders above it. When a buy fills, a sell is placed one spacing above it. When a sell fills, a buy is placed one spacing below. Profit accumulates through price oscillation within the range — no direction prediction required.

## Setup

1. Configure Coinbase publisher credentials in Seren Desktop Settings → Publisher MCPs (desktop sidecar/keychain flow)
2. Copy `.env.example` to `.env` and set `SEREN_API_KEY` (`SEREN_DESKTOP_PUBLISHER_AUTH=true` is recommended)
3. Optional legacy fallback: set `SEREN_DESKTOP_PUBLISHER_AUTH=false` and fill `CB_ACCESS_*` values
4. Copy `config.example.json` to `config.json` and configure your grid parameters
5. Install dependencies: `pip install -r requirements.txt`
6. Run: `python scripts/agent.py`

## SerenDB Persistence (MCP-native)

Set these optional environment variables in `.env`:

- `SERENDB_PROJECT_NAME` (default auto target: `coinbase`)
- `SERENDB_DATABASE` (default auto target: `coinbase`)
- `SERENDB_BRANCH` (optional)
- `SERENDB_REGION` (default: `aws-us-east-1`)
- `SERENDB_AUTO_CREATE` (default: `true`)
- `SEREN_MCP_COMMAND` (default: `seren-mcp`)

Persistence is best-effort: if SerenDB/MCP is unavailable, trading still runs and logs locally.

## Configuration

See `config.example.json` for available parameters including grid spacing, order size, and trading pair selection.

## Disclaimer

This bot trades real money. Use at your own risk. Past performance does not guarantee future results.
