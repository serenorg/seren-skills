---
name: grid-trader
description: "Automated grid trading bot for Kraken — profits from BTC volatility using a mechanical, non-directional strategy"
---

# Kraken Grid Trader

Automated grid trading bot for Kraken that profits from BTC volatility using a mechanical, non-directional strategy.

## What This Skill Provides

- Automated Kraken grid trading with dry-run and live modes
- Pair selection support (single pair or candidate list)
- JSONL logs for setup, orders, fills, positions, and errors
- MCP-native SerenDB persistence for sessions, events, orders, fills, and position snapshots

## What is Grid Trading?

Grid trading places buy and sell orders at regular price intervals (the "grid"). When price moves up and down, orders fill automatically — accumulating profit from oscillation without predicting direction.

## Setup

1. Copy `.env.example` to `.env` and fill in your Seren API credentials
2. Copy `config.example.json` to `config.json` and configure your grid parameters
3. Install dependencies: `pip install -r requirements.txt`
4. Run: `python scripts/agent.py`

## SerenDB Persistence (MCP-native)

Set these optional environment variables in `.env`:

- `SERENDB_PROJECT_NAME` (default auto target: `krakent`)
- `SERENDB_DATABASE` (default auto target: `krakent`)
- `SERENDB_BRANCH` (optional)
- `SERENDB_REGION` (default: `aws-us-east-1`)
- `SERENDB_AUTO_CREATE` (default: `true`)
- `SEREN_MCP_COMMAND` (default: `seren-mcp`)

Persistence is best-effort: if SerenDB/MCP is unavailable, trading still runs and logs locally.

## Configuration

See `config.example.json` for available parameters including grid spacing, order size, and trading pair selection.

## Disclaimer

This bot trades real money. Use at your own risk. Past performance does not guarantee future results.
