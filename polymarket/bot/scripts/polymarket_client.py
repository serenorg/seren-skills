"""
Polymarket Client - Wrapper for Polymarket CLOB API

Uses ``py-clob-client`` (via DirectClobTrader from scripts/polymarket_live.py) for
trading operations and the ``polymarket-data`` Seren publisher for market discovery.
"""

import json
import sys
from pathlib import Path
from typing import Dict, List, Any, Optional

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from seren_client import SerenClient


class PolymarketClient:
    """Client for Polymarket CLOB API.

    Market discovery uses the ``polymarket-data`` Seren publisher.
    Trading operations use ``DirectClobTrader`` from ``polymarket_live.py``
    (wraps ``py-clob-client`` for local EIP-712 signing).
    """

    def __init__(
        self,
        seren_client: SerenClient,
        dry_run: bool = False,
        **_ignored: Any,
    ):
        """
        Initialize Polymarket client.

        Args:
            seren_client: Seren client instance (used for market data publisher).
            dry_run: When True, skip DirectClobTrader init (no credentials needed).
        """
        self.seren = seren_client
        self._trader: Any = None

        if not dry_run:
            try:
                from polymarket_live import DirectClobTrader

                self._trader = DirectClobTrader(
                    skill_root=Path(__file__).resolve().parents[1],
                    client_name="polymarket-bot",
                )
            except RuntimeError:
                # Credentials missing — trading will fail but market scanning works.
                pass

    # -- Market discovery (polymarket-data publisher) -------------------------

    def get_markets(
        self,
        limit: int = 500,
        active: bool = True,
        sort_by: str = "volume",
        end_date_min: str = "",
        end_date_max: str = "",
    ) -> List[Dict]:
        """
        Get list of prediction markets via polymarket-data publisher.

        Args:
            limit: Max markets to return
            active: Only active markets
            sort_by: Sort field for Gamma API (volume, liquidity, or default)
            end_date_min: ISO date string; exclude markets resolving before this
            end_date_max: ISO date string; exclude markets resolving after this

        Returns:
            List of market dicts
        """
        params = f"?limit={limit}&active={'true' if active else 'false'}&closed=false"
        if sort_by:
            params += f"&order={sort_by}&ascending=false"
        if end_date_min:
            params += f"&end_date_min={end_date_min}"
        if end_date_max:
            params += f"&end_date_max={end_date_max}"

        response = self.seren.call_publisher(
            publisher='polymarket-data',
            method='GET',
            path=f'/markets{params}'
        )

        markets: List[Dict] = []

        market_list = response.get('body', [])
        if not market_list and 'data' in response:
            market_list = response.get('data', [])

        for market_data in market_list:
            if market_data.get('closed', False):
                continue

            market_id = market_data.get('conditionId') or market_data.get('id')
            question = market_data.get('question', '')

            clob_token_ids_str = market_data.get('clobTokenIds', '[]')
            try:
                token_ids = json.loads(clob_token_ids_str) if isinstance(clob_token_ids_str, str) else clob_token_ids_str
            except Exception:
                token_ids = []

            if not token_ids:
                continue

            yes_token_id = token_ids[0]
            no_token_id = token_ids[1] if len(token_ids) > 1 else None

            raw_outcome_prices = market_data.get('outcomePrices')
            price = 0.5
            price_source = 'gamma_fallback'
            outcome_prices_csv = ''

            if isinstance(raw_outcome_prices, str):
                try:
                    outcome_prices = json.loads(raw_outcome_prices)
                except Exception:
                    outcome_prices = [p.strip() for p in raw_outcome_prices.split(',') if p.strip()]
            elif isinstance(raw_outcome_prices, list):
                outcome_prices = raw_outcome_prices
            else:
                outcome_prices = []

            if outcome_prices:
                try:
                    parsed_outcome_prices = [float(str(price_part).strip()) for price_part in outcome_prices]
                    price = parsed_outcome_prices[0]
                    price_source = 'gamma'
                    outcome_prices_csv = ','.join(str(price_part) for price_part in parsed_outcome_prices)
                except Exception:
                    price = 0.5
                    price_source = 'gamma_fallback'

            # If the Gamma outcomePrices is a stale 0.5/0.5 seed, try CLOB
            # fields that the Gamma API already includes in the response.
            is_stale = abs(price - 0.5) < 0.02

            if is_stale:
                # Try lastTradePrice from the CLOB
                ltp = market_data.get('lastTradePrice')
                if ltp is not None:
                    try:
                        ltp_val = float(ltp)
                        if 0.0 < ltp_val < 1.0 and abs(ltp_val - 0.5) >= 0.02:
                            price = ltp_val
                            price_source = 'clob_last_trade'
                            is_stale = False
                    except (ValueError, TypeError):
                        pass

            if is_stale:
                # Try bestBid / bestAsk midpoint from the Gamma response
                best_bid = market_data.get('bestBid')
                best_ask = market_data.get('bestAsk')
                if best_bid is not None and best_ask is not None:
                    try:
                        bid_val = float(best_bid)
                        ask_val = float(best_ask)
                        if bid_val > 0 and ask_val > 0:
                            mid = (bid_val + ask_val) / 2.0
                            if abs(mid - 0.5) >= 0.02:
                                price = mid
                                price_source = 'clob_book_mid'
                                is_stale = False
                    except (ValueError, TypeError):
                        pass

            if is_stale:
                price_source = 'stale_gamma'

            volume = float(market_data.get('volume', 0))
            liquidity = float(market_data.get('liquidity', 0))
            end_date = market_data.get('endDateIso') or market_data.get('end_date_iso', '')

            if liquidity < 100:
                continue

            markets.append({
                'market_id': market_id,
                'question': question,
                'token_id': yes_token_id,
                'no_token_id': no_token_id,
                'outcomePrices': outcome_prices_csv,
                'price': price,
                'price_source': price_source,
                'volume': volume,
                'liquidity': liquidity,
                'end_date': end_date,
            })

        return markets[:limit]

    # -- Trading operations (DirectClobTrader / py-clob-client) ---------------

    def _require_trader(self) -> Any:
        if self._trader is None:
            raise RuntimeError(
                "Trading requires py-clob-client credentials. "
                "Set POLY_PRIVATE_KEY, POLY_API_KEY, POLY_PASSPHRASE, and POLY_SECRET."
            )
        return self._trader

    def place_order(
        self,
        token_id: str,
        side: str,
        size: float,
        price: float,
        order_type: str = 'GTC',
    ) -> Dict:
        """Place an order via py-clob-client (local EIP-712 signing)."""
        del order_type
        from polymarket_live import fetch_book, fetch_fee_rate_bps, snap_price

        trader = self._require_trader()
        book = fetch_book(token_id)
        tick_size = str(book.get("tick_size", "0.01"))
        neg_risk = bool(book.get("neg_risk", False))
        fee_rate_bps = fetch_fee_rate_bps(token_id)
        snapped_price = snap_price(price, tick_size, side)
        return trader.create_order(
            token_id=token_id,
            side=side,
            price=snapped_price,
            size=size,
            tick_size=tick_size,
            neg_risk=neg_risk,
            fee_rate_bps=fee_rate_bps,
        )

    def cancel_order(self, order_id: str) -> Dict:
        """Cancel all open orders."""
        trader = self._require_trader()
        return trader.cancel_all()

    def get_positions(self) -> List[Dict]:
        """Get current positions."""
        trader = self._require_trader()
        result = trader.get_positions()
        return result if isinstance(result, list) else []

    def get_open_orders(self, market: Optional[str] = None) -> List[Dict]:
        """Get open orders."""
        trader = self._require_trader()
        result = trader.get_orders()
        if isinstance(result, list) and market:
            return [o for o in result if o.get('market') == market]
        return result if isinstance(result, list) else []

    def get_balance(self) -> float:
        """Get USDC balance from Polymarket wallet."""
        try:
            trader = self._require_trader()
            return trader.get_cash_balance()
        except Exception:
            return 0.0

    def _get_book_levels(self, token_id: str):
        """Get best bid/ask from CLOB.

        Uses parse_book_payload which correctly handles the Polymarket CLOB
        sort order (bids ascending, asks descending) by scanning for the
        true max bid and min ask.
        """
        from polymarket_live import fetch_book
        book = fetch_book(token_id)
        return book.get('best_bid', 0.0), book.get('best_ask', 0.0)

    def get_price(self, token_id: str, side: str) -> float:
        """Get current price for a token from the CLOB orderbook."""
        best_bid, best_ask = self._get_book_levels(token_id)
        if side.upper() == 'BUY':
            return best_ask
        else:
            return best_bid

    def get_book_metrics(self, token_id: str) -> Dict:
        """Get spread and visible depth from the CLOB orderbook.

        Returns:
            Dict with keys: best_bid, best_ask, spread, mid, bid_depth_usd, ask_depth_usd
        """
        from polymarket_live import fetch_book
        book = fetch_book(token_id)

        # best_bid/best_ask are correctly computed by parse_book_payload
        # (scans for max bid and min ask regardless of sort order)
        best_bid = book.get('best_bid', 0.0)
        best_ask = book.get('best_ask', 0.0)
        mid = (best_bid + best_ask) / 2.0 if best_bid and best_ask else 0.0
        spread = (best_ask - best_bid) if best_bid and best_ask else 0.0

        # Get raw levels for depth calculation
        raw = book.get('raw', {})
        bids = raw.get('bids', []) if isinstance(raw, dict) else []
        asks = raw.get('asks', []) if isinstance(raw, dict) else []

        # Visible depth in USD: sum(price * size) for each level
        bid_depth = sum(
            float(level.get('price', 0)) * float(level.get('size', 0))
            for level in bids
        )
        ask_depth = sum(
            float(level.get('price', 0)) * float(level.get('size', 0))
            for level in asks
        )

        return {
            'best_bid': best_bid,
            'best_ask': best_ask,
            'spread': spread,
            'mid': mid,
            'bid_depth_usd': bid_depth,
            'ask_depth_usd': ask_depth,
        }

    def get_midpoint(self, token_id: str, max_spread: float = 0.50) -> float:
        """Get midpoint price (average of best bid and ask).

        Returns 0.0 when the book spread exceeds *max_spread* — a wide
        spread (e.g. bid=0.001, ask=0.999 → spread=0.998) produces a
        meaningless ~0.50 midpoint and must not overwrite a valid Gamma
        price.
        """
        best_bid, best_ask = self._get_book_levels(token_id)
        if best_bid and best_ask:
            if best_ask - best_bid > max_spread:
                return 0.0
            return (best_bid + best_ask) / 2.0
        return best_bid or best_ask
