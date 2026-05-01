"""Critical tests for prophet-polymarket-edge.

Each test maps to one acceptance-criterion line in issue #452. Tests that
would only re-cover already-covered behavior are intentionally omitted.

Coverage map (issue #452 -> test):

- AC #2 (schema idempotent)               -> test_schema_ddl_is_idempotent
- AC #5 (set difference excludes Prophet) -> test_watchlist_excludes_prophet_open_markets
- AC #6 (auth split deep links)           -> test_watchlist_suppresses_deep_links_when_unauthenticated
- AC #7 (no /api/oracle/actionable)       -> test_intelligence_client_does_not_expose_actionable
- AC #7 (verbatim labels)                 -> test_consensus_context_uses_verbatim_labels
                                             test_consensus_context_never_says_recommended_side
- AC #8 (--yes-live rejected)             -> test_yes_live_is_rejected_at_v1
- AC #9 (consensus context block frozen)  -> test_consensus_context_block_is_frozen_verbatim
"""

from __future__ import annotations

import importlib.util
import re
import sqlite3
import sys
from pathlib import Path
from typing import List


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "agent.py"
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "serendb_schema.sql"


def _load_agent_module():
    spec = importlib.util.spec_from_file_location("prophet_polymarket_edge_agent", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Schema idempotency uses an in-memory SQLite shim. We only assert that the
# DDL is parseable, idempotent, and creates the expected tables — not
# Postgres-only behavior. The Postgres `BIGSERIAL`/`SERIAL`/`TIMESTAMPTZ`/
# `JSONB`/`TEXT[]` types are translated to SQLite-compatible equivalents
# for this test only.
# ---------------------------------------------------------------------------


def _to_sqlite(sql: str) -> str:
    sql = re.sub(r"BIGSERIAL", "INTEGER", sql)
    sql = re.sub(r"SERIAL", "INTEGER", sql)
    sql = re.sub(r"TIMESTAMPTZ", "TEXT", sql)
    sql = re.sub(r"JSONB", "TEXT", sql)
    sql = re.sub(r"TEXT\[\]", "TEXT", sql)
    sql = re.sub(r"NUMERIC", "REAL", sql)
    sql = re.sub(r"DEFAULT NOW\(\)", "DEFAULT CURRENT_TIMESTAMP", sql)
    sql = re.sub(r"CREATE SCHEMA IF NOT EXISTS [^;]+;", "", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\bprophet_polymarket_edge\.", "", sql)
    sql = re.sub(r"REFERENCES\s+[A-Za-z_]+\([^)]+\)", "", sql)
    sql = re.sub(
        r"PRIMARY\s+KEY\s*,",
        "PRIMARY KEY AUTOINCREMENT,",
        sql,
        flags=re.IGNORECASE,
    )
    return sql


def _expected_tables() -> List[str]:
    return [
        "wallet_identities",
        "audit_runs",
        "audit_findings",
        "recommendations",
        "cost_estimate_gates",
        "telemetry_events",
    ]


def test_schema_ddl_is_idempotent() -> None:
    agent = _load_agent_module()
    raw = agent.storage_bootstrap_sql("prophet_polymarket_edge")
    assert raw, "expected at least one DDL statement"
    sqlite_sql = _to_sqlite("\n".join(s + ";" for s in raw))
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(sqlite_sql)
        # Apply twice to confirm idempotence.
        conn.executescript(sqlite_sql)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        names = {r[0] for r in rows}
    finally:
        conn.close()
    expected = set(_expected_tables())
    missing = expected - names
    assert not missing, f"missing tables: {missing}"


# ---------------------------------------------------------------------------
# Watchlist computation
# ---------------------------------------------------------------------------


def _sample_divergence_rows() -> List[dict]:
    return [
        {
            "canonical_id": "open-on-prophet",
            "description": "Will the Lakers win the NBA championship in 2026?",
            "platform_count": 4,
            "liquidity_usd": 50000,
            "divergence_bps": 350,
            "polymarket_url": "https://polymarket.com/event/lakers-2026",
            "polymarket_price": 0.31,
        },
        {
            "canonical_id": "long-tail-1",
            "description": "Will France ratify a new EU climate treaty by Q3 2026?",
            "platform_count": 3,
            "liquidity_usd": 25000,
            "divergence_bps": 480,
            "polymarket_url": "https://polymarket.com/event/eu-treaty",
            "polymarket_price": 0.22,
        },
        {
            "canonical_id": "long-tail-2",
            "description": "Will Brazil's senate confirm a new central bank chair by July?",
            "platform_count": 5,
            "liquidity_usd": 80000,
            "divergence_bps": 220,
            "polymarket_url": "https://polymarket.com/event/brazil-cb",
            "polymarket_price": 0.45,
        },
    ]


def _sample_consensus_by_id() -> dict:
    return {
        "open-on-prophet": {"consensus_probability": 0.27, "consensus_direction": "no", "freshness_note": "fresh <30m"},
        "long-tail-1": {"consensus_probability": 0.18, "consensus_direction": "no", "freshness_note": "fresh <2h"},
        "long-tail-2": {"consensus_probability": 0.50, "consensus_direction": "yes", "freshness_note": "fresh <1h"},
    }


def test_watchlist_excludes_prophet_open_markets() -> None:
    agent = _load_agent_module()
    open_titles = agent.normalize_prophet_open_titles(
        [{"title": "Will the Lakers win the NBA championship in 2026?"}]
    )
    candidates = agent.compute_watchlist_candidates(
        divergence_rows=_sample_divergence_rows(),
        consensus_by_id=_sample_consensus_by_id(),
        prophet_open_titles=open_titles,
        watchlist_limit=5,
        min_platforms=3,
        min_liquidity_usd=10000,
    )
    ids = [c.canonical_id for c in candidates]
    assert "open-on-prophet" not in ids
    assert "long-tail-1" in ids
    assert "long-tail-2" in ids


def test_watchlist_renders_consensus_context_block() -> None:
    agent = _load_agent_module()
    candidates = agent.compute_watchlist_candidates(
        divergence_rows=_sample_divergence_rows(),
        consensus_by_id=_sample_consensus_by_id(),
        prophet_open_titles=[],
        watchlist_limit=5,
        min_platforms=3,
        min_liquidity_usd=10000,
    )
    rendered = agent.render_watchlist(
        candidates=candidates,
        prophet_authenticated=True,
    )
    # The verbatim consensus-context block is rendered for each entry.
    assert agent.CONSENSUS_CONTEXT_BLOCK.split("\n")[0] in rendered
    assert "Tranche 1 watchlist" in rendered


def test_watchlist_suppresses_deep_links_when_unauthenticated() -> None:
    agent = _load_agent_module()
    candidates = agent.compute_watchlist_candidates(
        divergence_rows=_sample_divergence_rows(),
        consensus_by_id=_sample_consensus_by_id(),
        prophet_open_titles=[],
        watchlist_limit=5,
        min_platforms=3,
        min_liquidity_usd=10000,
    )
    rendered = agent.render_watchlist(
        candidates=candidates,
        prophet_authenticated=False,
    )
    assert "[Create this market on Prophet]" not in rendered
    # The watchlist body itself is still rendered (auth split allows reading).
    assert "Tranche 1 watchlist" in rendered


# ---------------------------------------------------------------------------
# Surface C invariants
# ---------------------------------------------------------------------------


def test_consensus_context_uses_verbatim_labels() -> None:
    agent = _load_agent_module()
    rows = agent.compute_consensus_context_rows(
        divergence_rows=_sample_divergence_rows(),
        consensus_by_id=_sample_consensus_by_id(),
        consensus_context_limit=10,
    )
    rendered = agent.render_consensus_context(rows)
    assert "consensus probability:" in rendered
    assert "consensus direction:" in rendered
    assert "current Polymarket price:" in rendered


def test_consensus_context_never_says_recommended_side() -> None:
    agent = _load_agent_module()
    rows = agent.compute_consensus_context_rows(
        divergence_rows=_sample_divergence_rows(),
        consensus_by_id=_sample_consensus_by_id(),
        consensus_context_limit=10,
    )
    rendered = agent.render_consensus_context(rows)
    forbidden = ["recommended side", "recommended-side", "take the other side", "fair-value anchor"]
    for phrase in forbidden:
        assert phrase.lower() not in rendered.lower(), f"forbidden phrase appeared: {phrase!r}"


def test_intelligence_client_does_not_expose_actionable() -> None:
    """`/api/oracle/actionable` is out of scope at v1. The client must
    not have a method that calls it, and no executable code path in the
    source should reference the route. The route name is allowed to appear
    in comments/docstrings (that documents why it is excluded).
    """
    agent = _load_agent_module()
    client_methods = {name for name in dir(agent.PolymarketIntelligence) if not name.startswith("_")}
    forbidden_method_names = {"actionable", "get_actionable", "fetch_actionable"}
    assert not (client_methods & forbidden_method_names), (
        "PolymarketIntelligence must not expose /api/oracle/actionable in v1"
    )
    # Strip docstrings + comments and assert the route isn't called.
    src = _strip_comments_and_docstrings(SCRIPT_PATH.read_text(encoding="utf-8"))
    assert "actionable" not in src, (
        "/api/oracle/actionable must not be referenced from executable code in v1"
    )


def _strip_comments_and_docstrings(src: str) -> str:
    import io
    import tokenize

    out = []
    tokens = list(tokenize.generate_tokens(io.StringIO(src).readline))
    for i, tok in enumerate(tokens):
        if tok.type == tokenize.COMMENT:
            continue
        if tok.type == tokenize.STRING:
            # Skip module/class/function docstrings: STRING tokens that are
            # the first statement in a code block, sitting on their own line
            # at column 0 of the logical statement.
            prev_significant = next(
                (
                    t
                    for t in reversed(tokens[:i])
                    if t.type not in (tokenize.NL, tokenize.NEWLINE, tokenize.COMMENT, tokenize.INDENT, tokenize.DEDENT, tokenize.ENCODING)
                ),
                None,
            )
            if prev_significant is None or prev_significant.string in (":", ""):
                # Likely a docstring — drop it.
                continue
        out.append(tok.string)
    return " ".join(out)


# ---------------------------------------------------------------------------
# Live-execution gate (--yes-live must be rejected)
# ---------------------------------------------------------------------------


def test_yes_live_is_rejected_at_v1(monkeypatch, capsys) -> None:
    agent = _load_agent_module()
    rc = agent.main(["--yes-live"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "rejected at v1 launch" in err


# ---------------------------------------------------------------------------
# Renderer-invariant freeze for the §6.1 consensus context block
# ---------------------------------------------------------------------------


def test_consensus_context_block_is_frozen_verbatim() -> None:
    agent = _load_agent_module()
    assert agent.CONSENSUS_CONTEXT_BLOCK == (
        "Cross-platform consensus context, where available.\n"
        "This is not Prophet's quote, not a trading signal, and not a claim\n"
        "that the AI House will price above or below it. Use it only as\n"
        "background context when deciding whether the market is worth creating."
    )
