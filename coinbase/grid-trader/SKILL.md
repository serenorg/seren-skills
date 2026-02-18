---
name: Coinbase Grid Trader
description: "Automated grid trading bot for Coinbase Exchange — profits from price oscillation using a mechanical, non-directional strategy"
kind: agent
runtime: python
author: Seren AI
version: 1.0.0
tags: [trading, crypto, coinbase, grid-bot]
publishers: [coinbase, seren-models]
cost_estimate: "$0.50-2.00 per trading cycle"
---

# Coinbase Grid Trader

Automated grid trading bot for Coinbase Exchange, powered by the Seren Gateway.

## What is Grid Trading?

Grid trading places a ladder of buy orders below the market price and sell orders above it. When a buy fills, a sell is placed one spacing above it. When a sell fills, a buy is placed one spacing below. Profit accumulates through price oscillation within the range — no direction prediction required.

## Setup

1. Copy `.env.example` to `.env` and fill in your Seren API credentials
2. Copy `config.example.json` to `config.json` and configure your grid parameters
3. Install dependencies: `pip install -r requirements.txt`
4. Run: `python scripts/agent.py`

## Configuration

See `config.example.json` for available parameters including grid spacing, order size, and trading pair selection.

## Disclaimer

This bot trades real money. Use at your own risk. Past performance does not guarantee future results.
