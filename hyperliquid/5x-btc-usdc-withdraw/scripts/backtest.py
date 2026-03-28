#!/usr/bin/env python3
"""270-day liquidation risk backtest for leveraged BTC positions.

Uses CoinGecko publisher via Seren gateway for historical BTC prices.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_API_BASE = "https://api.serendb.com"
COINGECKO_PUBLISHER = "coingecko-serenai"
BACKTEST_DAYS = 270
LOOKFORWARD_DAYS = 30

# Hyperliquid approximate maintenance margin rates by leverage tier
MAINT_RATES = {5: 0.03, 10: 0.03, 20: 0.025, 25: 0.02, 50: 0.01}


def _fetch_btc_prices(api_base: str, api_key: str, days: int = BACKTEST_DAYS) -> list[tuple[str, float]]:
    """Fetch daily BTC prices from CoinGecko via Seren publisher."""
    url = f"{api_base}/publishers/{COINGECKO_PUBLISHER}/call"
    body = json.dumps({
        "method": "GET",
        "path": f"/api/v3/coins/bitcoin/market_chart?vs_currency=usd&days={days}&interval=daily",
    }).encode()
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    req = Request(url, data=body, headers=headers, method="POST")
    with urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())

    result_body = data.get("body", data)
    prices_raw = result_body.get("prices", [])

    prices = []
    for ts, price in prices_raw:
        dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        prices.append((dt, price))
    return prices


def run_backtest(prices: list[tuple[str, float]], leverage: int = 5) -> dict[str, Any]:
    """Run liquidation risk backtest on historical BTC prices.

    For each day, simulate opening a long position and check if it would
    be liquidated within the next LOOKFORWARD_DAYS days.
    """
    maint_rate = MAINT_RATES.get(leverage, 0.03)
    liq_drop = (1 / leverage) - maint_rate  # max drop before liquidation

    liquidations = 0
    total_entries = 0
    days_survived = []
    liq_events = []
    max_drawdown = 0.0

    for i in range(len(prices) - 1):
        entry_date, entry_price = prices[i]
        liq_price = entry_price * (1 - liq_drop)
        liquidated = False

        for j in range(i + 1, min(i + LOOKFORWARD_DAYS + 1, len(prices))):
            check_date, check_price = prices[j]
            drop = (entry_price - check_price) / entry_price
            max_drawdown = max(max_drawdown, drop)

            if check_price <= liq_price:
                liquidations += 1
                days_survived.append(j - i)
                liq_events.append({
                    "entry_date": entry_date,
                    "liq_date": check_date,
                    "entry_price": round(entry_price, 0),
                    "liq_price": round(check_price, 0),
                    "drop_pct": round(drop * 100, 1),
                })
                liquidated = True
                break

        total_entries += 1

    # Worst drawdowns by window
    worst_7d = 0.0
    worst_30d = 0.0
    for i in range(len(prices)):
        for window, label in [(7, "7d"), (30, "30d")]:
            if i + window < len(prices):
                peak = prices[i][1]
                for k in range(i + 1, i + window + 1):
                    dd = (peak - prices[k][1]) / peak
                    if label == "7d":
                        worst_7d = max(worst_7d, dd)
                    else:
                        worst_30d = max(worst_30d, dd)

    liq_rate = liquidations / total_entries * 100 if total_entries else 0

    return {
        "leverage": leverage,
        "liq_threshold_pct": round(liq_drop * 100, 1),
        "backtest_days": len(prices),
        "date_range": f"{prices[0][0]} to {prices[-1][0]}",
        "btc_start": round(prices[0][1], 0),
        "btc_end": round(prices[-1][1], 0),
        "btc_min": round(min(p for _, p in prices), 0),
        "btc_max": round(max(p for _, p in prices), 0),
        "entries_tested": total_entries,
        "liquidations": liquidations,
        "liq_rate_pct": round(liq_rate, 1),
        "avg_days_to_liq": round(sum(days_survived) / len(days_survived), 1) if days_survived else None,
        "worst_7d_drawdown_pct": round(worst_7d * 100, 1),
        "worst_30d_drawdown_pct": round(worst_30d * 100, 1),
        "max_drawdown_pct": round(max_drawdown * 100, 1),
        "sample_liq_events": liq_events[:5],
        "risk_rating": "LOW" if liq_rate < 5 else ("MODERATE" if liq_rate < 15 else ("HIGH" if liq_rate < 30 else "VERY HIGH")),
    }


def format_backtest_report(result: dict[str, Any]) -> str:
    """Format backtest result as a human-readable report."""
    lines = [
        f"=== {result['leverage']}x LEVERAGE — {result['backtest_days']}-Day Liquidation Backtest ===",
        f"Period:              {result['date_range']}",
        f"BTC range:           ${result['btc_min']:,.0f} – ${result['btc_max']:,.0f}",
        f"Liquidation at:      {result['liq_threshold_pct']}% drop from entry",
        f"Entries tested:      {result['entries_tested']}",
        f"Liquidated (30d):    {result['liquidations']} ({result['liq_rate_pct']}%)",
        f"Risk rating:         {result['risk_rating']}",
        f"Worst 7d drawdown:   {result['worst_7d_drawdown_pct']}%",
        f"Worst 30d drawdown:  {result['worst_30d_drawdown_pct']}%",
    ]
    if result["avg_days_to_liq"]:
        lines.append(f"Avg days to liq:     {result['avg_days_to_liq']}")
    return "\n".join(lines)
