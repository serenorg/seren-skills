"""Critical-only tests for prophet.client.

Coverage:
  - test_markets_for_dedup_uses_relay_connection_and_unwraps_edges:
    Pins the live Prophet schema shape for the `markets` root query.
    Three drift axes are covered in this one test because they all
    travel together — a regression in any axis silently disables
    auto-pair and ships the bot back to the 0/50 manual-creation
    failure mode.

      Axis 1 (#614): response shape. Prophet exposes
        `markets(input: MarketsInput): MarketConnection!` where
        `MarketConnection { edges{node,cursor}, pageInfo, totalCount }`.
        Legacy `markets { id slug ... }` was rejected with HTTP 422 +
        `Cannot query field "id" on type "MarketConnection"`.
      Axis 2 (#621): input pagination. `MarketsInput` does NOT accept
        `limit` — it uses Relay-style `first: Int` cursor pagination.
        Surfaced as `unknown field` at `variable.input.limit`.
      Axis 3 (#621): input filter. `status` is not a top-level field of
        `MarketsInput`; it lives at `filter.status: MarketStatus` and
        the enum is uppercase (`OPEN`, not `"active"`).

    The test pins both the wire-shape (query string and variables
    structure Prophet's schema accepts) and the return-shape
    (`find_matching_prophet_markets` reads flat `{id, question}` dicts).
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

    # --- Wire-shape: response selection set ---------------------------
    sent_query = stub_transport.calls[0]["query"]
    # Selection set must use the Relay connection pattern; the legacy
    # `markets { id slug question resolutionDate }` form was rejected
    # with HTTP 422 + `Cannot query field "id" on type "MarketConnection"`.
    assert "edges" in sent_query, "markets query must select edges (Relay connection)"
    assert "node" in sent_query, "markets query must select node fields under edges"
    # The leaf fields the caller depends on must still be requested.
    for field in ("id", "slug", "question", "resolutionDate"):
        assert field in sent_query, f"markets query must select {field!r} on node"

    # --- Wire-shape: MarketsInput variables (#621) --------------------
    # Pinned schema: MarketsInput = {first, after, last, before, filter,
    # sort}. No top-level `limit` or `status`. Filter is
    # `{status: MarketStatus}` and the enum is uppercase. The legacy
    # `{limit, status: "active"}` shape produced HTTP 422 +
    # `unknown field` at path `variable.input.limit` after #614
    # unblocked the response side.
    sent_input = stub_transport.calls[0]["variables"]["input"]
    assert sent_input["first"] == 200, "input.first must carry the limit value"
    assert sent_input["filter"]["status"] == "OPEN", (
        "input.filter.status must be the uppercase MarketStatus enum"
    )
    # Legacy field names must not leak — Prophet rejects them at validation.
    assert "limit" not in sent_input, "MarketsInput has no `limit` field"
    assert "status" not in sent_input, "MarketsInput.status moved under filter"

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
