"""Critical tests for prophet-polymarket-edge.

Each test maps to one acceptance-criterion line in issue #452. Tests that
would only re-cover already-covered behavior are intentionally omitted.

Coverage map (issue #452 -> test):

- AC #2 (schema idempotent)               -> test_schema_ddl_is_idempotent
- AC #3 (disclosure gate blocks decline)  -> test_disclosure_gate_blocks_on_decline
- AC #4/#11 (Surface B disclosure render) -> test_watchlist_renders_surface_b_disclosure_above_list
                                             test_watchlist_suppresses_deep_links_when_disclosure_row_missing
- AC #5 (set difference excludes Prophet) -> test_watchlist_excludes_prophet_open_markets
- AC #6 (auth split deep links)           -> test_watchlist_suppresses_deep_links_when_unauthenticated
- AC #7 (no /api/oracle/actionable)       -> test_intelligence_client_does_not_expose_actionable
- AC #7 (verbatim labels)                 -> test_consensus_context_uses_verbatim_labels
                                             test_consensus_context_never_says_recommended_side
- AC #8 (--yes-live rejected)             -> test_yes_live_is_rejected_at_v1
- AC #9 (renderer invariants)             -> test_disclosure_blocks_are_frozen_verbatim
- AC #11 (purge preserves ledger)         -> test_purge_preserves_disclosure_ledger
"""

from __future__ import annotations

import importlib.util
import io
import re
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List

import pytest


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
# DDL is parseable, idempotent, and creates the 8 tables — not Postgres-only
# behavior. The Postgres `BIGSERIAL`/`SERIAL`/`TIMESTAMPTZ`/`JSONB`/`TEXT[]`
# types are translated to SQLite-compatible equivalents for this test only.
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
        "disclosure_acknowledgements",
        "cost_estimate_gates",
        "surface_b_benefit_disclosures",
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
# Disclosure-gate behavior. We mock psycopg_connect so the gate's DB writes
# go to an in-memory recorder. The verbatim copy is asserted below in a
# dedicated test.
# ---------------------------------------------------------------------------


class FakeCursor:
    def __init__(self, store: Dict[str, List[tuple]]):
        self._store = store
        self._last_query = ""

    def execute(self, query: str, params: Any = None):
        self._last_query = query
        ql = " ".join(query.split()).lower()
        if ql.startswith("insert into") and "telemetry_events" in ql:
            self._store.setdefault("telemetry_events", []).append(params)
        elif ql.startswith("insert into") and "disclosure_acknowledgements" in ql:
            self._store.setdefault("disclosure_acknowledgements", []).append(params)
        elif ql.startswith("insert into") and "audit_runs" in ql and "returning" in ql:
            run_id = len(self._store.setdefault("audit_runs", [])) + 1
            self._store["audit_runs"].append((run_id, params))
            self._returning_value = (run_id,)
        elif ql.startswith("insert into") and "surface_b_benefit_disclosures" in ql:
            self._store.setdefault("surface_b_benefit_disclosures", []).append(params)
        elif ql.startswith("insert into") and "recommendations" in ql:
            self._store.setdefault("recommendations", []).append(params)
        elif ql.startswith("select 1 from"):
            self._store["acknowledged_check_count"] = self._store.get("acknowledged_check_count", 0) + 1
        else:
            self._store.setdefault("other", []).append((query, params))

    def fetchone(self):
        if hasattr(self, "_returning_value"):
            v = self._returning_value
            del self._returning_value
            return v
        return None

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConnection:
    def __init__(self, store: Dict[str, List[tuple]]):
        self._store = store

    def cursor(self):
        return FakeCursor(self._store)

    def commit(self):
        self._store["commits"] = self._store.get("commits", 0) + 1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@contextmanager
def patched_connect(monkeypatch, agent, store: Dict[str, List[tuple]]):
    def fake(dsn: str):
        return FakeConnection(store)

    monkeypatch.setattr(agent, "psycopg_connect", fake)
    yield


def test_disclosure_gate_blocks_on_decline(monkeypatch) -> None:
    agent = _load_agent_module()
    store: Dict[str, List[tuple]] = {}

    storage_result = {
        "schema_name": "prophet_polymarket_edge",
        "connection_string": "postgresql://example/prophet_polymarket_edge",
    }
    config = {
        "inputs": {"watchlist_limit": 5, "consensus_context_limit": 5, "min_platforms": 3, "min_liquidity_usd": 0},
        "secrets": {"SEREN_API_KEY": "test"},
    }

    class Args:
        json_output = False
        purge = False

    with patched_connect(monkeypatch, agent, store):
        result = agent.execute_run(
            config=config,
            args=Args(),
            storage_result=storage_result,
            user_id="user-1",
            disclosure_response_fn=lambda _text: False,
        )

    assert result["status"] == "disclosure_declined"
    # Exactly one telemetry event of type disclosure_declined was emitted,
    # and no Polymarket intelligence calls were made (no recommendations).
    telemetry = store.get("telemetry_events", [])
    assert len(telemetry) == 1
    assert telemetry[0][2] == "disclosure_declined"
    assert "recommendations" not in store
    assert "surface_b_benefit_disclosures" not in store


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


def _sample_consensus_by_id() -> Dict[str, dict]:
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


def test_watchlist_renders_surface_b_disclosure_above_list() -> None:
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
        surface_b_disclosure_persisted=True,
        prophet_authenticated=True,
    )
    # Disclosure must precede the watchlist body.
    disclosure_idx = rendered.index(agent.SURFACE_B_BENEFIT_DISCLOSURE)
    list_marker_idx = rendered.index("Tranche 1 watchlist")
    assert disclosure_idx < list_marker_idx
    # The verbatim consensus-context block is rendered for each entry.
    assert agent.CONSENSUS_CONTEXT_BLOCK.split("\n")[0] in rendered


def test_watchlist_suppresses_deep_links_when_disclosure_row_missing() -> None:
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
        surface_b_disclosure_persisted=False,
        prophet_authenticated=True,
    )
    assert "[Create this market on Prophet]" not in rendered
    assert "deep link suppressed" in rendered


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
        surface_b_disclosure_persisted=True,
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
    """`/api/oracle/actionable` is gated post-v1 (§13.14). The client must
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
# Renderer-invariant freeze for verbatim disclosure text
# ---------------------------------------------------------------------------


def test_disclosure_blocks_are_frozen_verbatim() -> None:
    agent = _load_agent_module()
    # The exact strings live in the source as constants; this test pins the
    # text and fails loudly if anyone edits them without an audit revision.
    assert agent.SURFACE_B_BENEFIT_DISCLOSURE == (
        "Launch-week note: Prophet benefits if you create markets from this\n"
        "watchlist because it helps populate Prophet's market book during\n"
        "Tranche 1. This list is sponsored content. You can read it without\n"
        "creating a market."
    )
    assert agent.CONSENSUS_CONTEXT_BLOCK == (
        "Cross-platform consensus context, where available.\n"
        "This is not Prophet's quote, not a trading signal, and not a claim\n"
        "that the AI House will price above or below it. Use it only as\n"
        "background context when deciding whether the market is worth creating."
    )
    assert "[Paid Prophet recommendation]" in agent.PAID_RECOMMENDATION_DISCLOSURE
    assert "Continue? y/n" in agent.PAID_RECOMMENDATION_DISCLOSURE


# ---------------------------------------------------------------------------
# --purge preserves the disclosure ledger (acceptance criterion #11)
# ---------------------------------------------------------------------------


def test_purge_preserves_disclosure_ledger() -> None:
    """--purge deletes audit content but never touches disclosure_acknowledgements.

    The table is the legal ledger required for 3-year retention (§13.20).
    We assert that no DELETE statement targets the table within the purge
    function body. The function may still mention the table name in a
    docstring/comment (that documents the intent).
    """
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    start = src.index("def purge_user_audit_content(")
    end = src.index("\ndef ", start + 1)
    body = src[start:end]
    code_only = _strip_comments_and_docstrings(body)
    assert "disclosure_acknowledgements" not in code_only, (
        "purge_user_audit_content must NOT touch disclosure_acknowledgements in code; "
        "the legal ledger is preserved per design doc §13.20"
    )
    # Sanity: the purge body does delete the audit-content tables.
    for tbl in ("audit_runs", "audit_findings", "recommendations", "surface_b_benefit_disclosures"):
        assert "DELETE FROM" in body and tbl in body
