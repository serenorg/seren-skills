---
name: Kraken Grid Trader
description: "Automated grid trading bot for Kraken — profits from BTC volatility using a mechanical, non-directional strategy"
kind: agent
runtime: python
author: Seren AI
version: 1.0.0
tags: [trading, crypto, kraken, grid-bot]
publishers: [kraken, seren-models]
cost_estimate: "$0.50-2.00 per trading cycle"
---

# Kraken Grid Trader

Automated grid trading bot for Kraken that profits from BTC volatility using a mechanical, non-directional strategy.

## What is Grid Trading?

Grid trading places buy and sell orders at regular price intervals (the "grid"). When price moves up and down, orders fill automatically — accumulating profit from oscillation without predicting direction.

## Setup

1. Copy `.env.example` to `.env` and fill in your Seren API credentials
2. Copy `config.example.json` to `config.json` and configure your grid parameters
3. Install dependencies: `pip install -r requirements.txt`
4. Run: `python scripts/agent.py`

## Configuration

See `config.example.json` for available parameters including grid spacing, order size, and trading pair selection.

## Disclaimer

This bot trades real money. Use at your own risk. Past performance does not guarantee future results.
