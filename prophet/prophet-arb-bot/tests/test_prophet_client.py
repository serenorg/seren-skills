"""Critical-only tests for prophet.client.

Coverage:
  - test_markets_for_dedup_uses_relay_connection_and_unwraps_edges:
    Pins the live Prophet schema shape for the `markets` root query (#614).
    Prophet's GraphQL exposes `markets(input: MarketsInput): MarketConnection!`
    where `MarketConnection { edges { node, cursor }, pageInfo, totalCount }`.
    The legacy query selected `id slug question resolutionDate` directly on
    `MarketConnection`, which Prophet rejects with a HTTP 422 +
    `GRAPHQL_VALIDATION_FAILED: Cannot query field "id" on type
    "MarketConnection"`. The fix sends the connection-shaped selection set
    and unwraps `edges[].node` so the caller contract
    (`find_matching_prophet_markets` expects a flat `[{id, question, ...}]`
    list) is preserved.

    A single test covers both legs of the contract: the wire shape going
    out (proves we ask for what Prophet's schema accepts) and the return
    shape coming back (proves callers don't break).
"""

from __future__ import annotations

from prophet.client import MinimalProphetClient


def test_markets_for_dedup_uses_relay_connection_and_unwraps_edges(
    stub_transport,
) -> None:
    # Connection-shaped response — mirrors what Prophet actually returns
    # for `markets(input: $input)`. Pinned shape in
    # tests/fixtures/prophet_schema.json: MarketConnection has only
    # {edges, pageInfo, totalCount}; per-market fields live under
    # MarketEdge.node (a Market).
    stub_transport.register_default(
        {
            "data": {
                "markets": {
                    "edges": [
                        {
                            "node": {
                                "id": "PRO-A",
                                "slug": "yankees-vs-orioles",
                                "question": "New York Yankees vs Baltimore Orioles",
                                "resolutionDate": "2026-05-18T22:00:00Z",
                            }
                        },
                        {
                            "node": {
                                "id": "PRO-B",
                                "slug": "btc-100k",
                                "question": "Will BTC hit $100k by year end?",
                                "resolutionDate": "2026-12-31T23:59:59Z",
                            }
                        },
                    ],
                    "pageInfo": {"hasNextPage": False},
                    "totalCount": 2,
                }
            }
        }
    )

    client = MinimalProphetClient(transport=stub_transport)
    result = client.markets_for_dedup(jwt="eyJ.fake.jwt", limit=200)

    # --- Wire-shape contract (what Prophet's schema accepts) ----------
    sent = stub_transport.calls[0]["query"]
    # The selection set must use the Relay connection pattern; the
    # legacy `markets { id slug question resolutionDate }` form is what
    # production rejected with HTTP 422.
    assert "edges" in sent, "markets query must select edges (Relay connection)"
    assert "node" in sent, "markets query must select node fields under edges"
    # The leaf fields the caller depends on must still be requested.
    for field in ("id", "slug", "question", "resolutionDate"):
        assert field in sent, f"markets query must select {field!r} on node"

    # --- Return-shape contract (what callers expect) ------------------
    # find_matching_prophet_markets reads `market.get("id")` and
    # `market.get("question")` on each row, so the wrapper must unwrap
    # edges[].node into a flat list of market dicts.
    assert isinstance(result, list)
    assert len(result) == 2
    assert result[0]["id"] == "PRO-A"
    assert result[0]["question"] == "New York Yankees vs Baltimore Orioles"
    assert result[1]["id"] == "PRO-B"
    # Caller does not look at edge wrappers — confirm they're stripped.
    assert "node" not in result[0]
    assert "edges" not in result[0]
