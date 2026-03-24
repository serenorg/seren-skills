"""Verify polymarket_client.get_markets uses CLOB price fields when
Gamma outcomePrices is a stale 0.5/0.5 seed."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CLIENT_PATH = "polymarket/bot/scripts/polymarket_client.py"
AGENT_PATH = "polymarket/bot/scripts/agent.py"


def _source(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def test_get_markets_checks_last_trade_price() -> None:
    """get_markets must check lastTradePrice when outcomePrices is stale."""
    source = _source(CLIENT_PATH)
    assert "lastTradePrice" in source, (
        "polymarket_client.py must check lastTradePrice as a CLOB fallback"
    )
    assert "clob_last_trade" in source, (
        "polymarket_client.py must set price_source='clob_last_trade'"
    )


def test_get_markets_checks_best_bid_ask() -> None:
    """get_markets must check bestBid/bestAsk midpoint as a second fallback."""
    source = _source(CLIENT_PATH)
    assert "bestBid" in source
    assert "bestAsk" in source
    assert "clob_book_mid" in source, (
        "polymarket_client.py must set price_source='clob_book_mid'"
    )


def test_stale_markets_get_stale_gamma_source() -> None:
    """Markets still at 0.5 after all fallbacks must get price_source='stale_gamma'."""
    source = _source(CLIENT_PATH)
    assert "stale_gamma" in source


def test_agent_rejects_stale_gamma_markets() -> None:
    """agent.py enrichment must reject markets with price_source='stale_gamma'."""
    source = _source(AGENT_PATH)
    assert "'stale_gamma'" in source or '"stale_gamma"' in source


def test_agent_accepts_clob_price_sources() -> None:
    """agent.py enrichment must accept clob_last_trade and clob_book_mid
    as valid price sources when CLOB midpoint enrichment fails."""
    source = _source(AGENT_PATH)
    assert "clob_last_trade" in source
    assert "clob_book_mid" in source
