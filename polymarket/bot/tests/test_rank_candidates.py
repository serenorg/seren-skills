"""Tests for rank_candidates price-diversity and slug-dedup logic."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

def _make_market(slug, price_yes, liquidity=100000, volume=50000, end_days=30):
    from datetime import datetime, timezone, timedelta
    end = datetime.now(timezone.utc) + timedelta(days=end_days)
    return {
        'market_slug': slug,
        'question': slug.replace('-', ' ').title(),
        'outcomePrices': f"{price_yes},{round(1-price_yes,4)}",
        'liquidity': str(liquidity),
        'volume': str(volume),
        'end_date': end.isoformat(),
        'token_id': f'tok_{slug}',
        'price': price_yes,
        'price_source': 'gamma',
    }


class _FakePolymarket:
    """Stub that returns a valid midpoint so enrichment succeeds."""
    def get_midpoint(self, token_id):
        return None  # force fallback to gamma price_source


def _make_agent():
    from agent import TradingAgent
    a = TradingAgent.__new__(TradingAgent)
    a.max_resolution_days = 365
    a.candidate_limit = 80
    a.analyze_limit = 80
    a.min_liquidity = 0
    a.stale_price_demotion = 0.1
    a.polymarket = _FakePolymarket()
    return a

class TestPriceDiversityScoring:
    def test_midrange_beats_extreme_at_equal_liquidity(self):
        agent = _make_agent()
        markets = [
            _make_market('longshot-team-wins', 0.02, liquidity=500000, volume=500000),
            _make_market('close-election-race', 0.40, liquidity=500000, volume=500000),
        ]
        ranked = agent.rank_candidates(markets, limit=10)
        slugs = [m['market_slug'] for m in ranked]
        assert slugs.index('close-election-race') < slugs.index('longshot-team-wins')

    def test_extreme_high_price_penalized(self):
        agent = _make_agent()
        markets = [
            _make_market('near-certain-outcome', 0.97, liquidity=500000, volume=500000),
            _make_market('contested-question', 0.60, liquidity=500000, volume=500000),
        ]
        ranked = agent.rank_candidates(markets, limit=10)
        slugs = [m['market_slug'] for m in ranked]
        assert slugs.index('contested-question') < slugs.index('near-certain-outcome')

class TestSlugGroupDedup:
    def test_caps_at_three_per_group(self):
        agent = _make_agent()
        markets = [
            _make_market(f'will-{c}-win-the-2026-fifa-world-cup', 0.02*(i+1),
                         liquidity=1000000-i*10000, volume=500000)
            for i, c in enumerate(['brazil','germany','france','argentina','spain',
                                   'england','portugal','italy','netherlands','belgium'])
        ]
        markets.append(_make_market('will-btc-hit-100k', 0.45, liquidity=200000, volume=100000))
        ranked = agent.rank_candidates(markets, limit=80)
        fifa_count = sum(1 for m in ranked if 'fifa' in m['market_slug'])
        assert fifa_count <= 3, f"Expected max 3 FIFA markets, got {fifa_count}"
        assert any('btc' in m['market_slug'] for m in ranked)
