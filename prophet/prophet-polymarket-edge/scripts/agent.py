#!/usr/bin/env python3
"""Runtime for prophet-polymarket-edge — Surface B watchlist + Surface C consensus context + live trading.

Read-only default (no `--yes-live`):

- Surface B (Tranche 1 watchlist): pull Prophet open markets via Prophet
  GraphQL, pull Polymarket consensus + divergence via
  seren-polymarket-intelligence, compute the set difference, render the
  top N candidates with the verbatim §6.1 consensus-context block.
- Surface C (Polymarket consensus context): for each divergent market,
  render Polymarket URL, current Polymarket price, consensus probability,
  consensus direction (verbatim labels), divergence in bps, freshness.

Live trading (`--yes-live`):

- Both trading-safety gates must pass (`scripts/trading_safety.py`).
- For each Surface C row with `consensus_direction` and `divergence_bps`
  >= the configured floor, plan a Kelly-bounded BUY at the consensus
  side, capped per-market by `risk.max_position_notional_usd`.
- Submit via `DirectClobTrader` (lifted from
  `polymarket/maker-rebate-bot/scripts/polymarket_live.py:2049-2187`).

Emergency exit: no unwind / flatten / cancel-all path is wired in v1 of
the trading wiring. The position-cap and Kelly-fraction gates bound
single-trade exposure, but operators must use the maker-rebate-bot
emergency-exit path or close manually if a position needs to unwind
before resolution.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# Force unbuffered stdout so piped/background output is visible immediately.
if not sys.stdout.isatty():
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)

import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


SKILL_NAME = "prophet-polymarket-edge"
DEFAULT_PROJECT_NAME = "prophet-polymarket-edge"
DEFAULT_DATABASE_NAME = "prophet_polymarket_edge"
DEFAULT_SCHEMA_NAME = "prophet_polymarket_edge"
DEFAULT_REGION = "aws-us-east-2"
DEFAULT_PROPHET_BASE_URL = "https://app.prophetmarket.ai"
DEFAULT_INTEL_BASE_URL = "https://api.serendb.com/publishers/seren-polymarket-intelligence"
DEFAULT_SEREN_DB_BASE_URL = "https://api.serendb.com/publishers/seren-db"
SEREN_SKILLS_DOCS_URL = "https://docs.serendb.com/skills.md"

CONSENSUS_CONTEXT_BLOCK = (
    "Cross-platform consensus context, where available.\n"
    "This is not Prophet's quote, not a trading signal, and not a claim\n"
    "that the AI House will price above or below it. Use it only as\n"
    "background context when deciding whether the market is worth creating."
)

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "serendb_schema.sql"

OPEN_MARKETS_QUERY = """
query OpenMarkets($limit: Int!) {
  openMarkets(limit: $limit) {
    id
    title
    resolutionRules
    resolutionDate
    category
    __typename
  }
}
""".strip()


class ProphetEdgeError(RuntimeError):
    """Base error for runtime failures."""


class StorageBootstrapError(ProphetEdgeError):
    """Raised when SerenDB project/database/schema bootstrap fails."""


class IntelligenceError(ProphetEdgeError):
    """Raised when seren-polymarket-intelligence calls fail."""


class ProphetGraphQLError(ProphetEdgeError):
    """Raised when the Prophet GraphQL API returns an error."""


class ExecutionGateViolation(ProphetEdgeError):
    """Raised when an execution path is attempted in v1."""


@dataclass
class SerenDbTarget:
    project_id: str
    branch_id: str
    database_name: str
    connection_string: str
    project_name: str
    branch_name: str
    created_project: bool = False
    created_database: bool = False


@dataclass
class WatchlistCandidate:
    canonical_id: str
    description: str
    resolution_criteria: Optional[str]
    consensus_probability: Optional[float]
    consensus_direction: Optional[str]
    divergence_bps: Optional[int]
    polymarket_price: Optional[float]
    polymarket_url: Optional[str]
    why_listed: str
    confidence: Optional[str]
    liquidity_usd: Optional[float]
    platform_count: Optional[int]


@dataclass
class ConsensusContextRow:
    canonical_id: str
    market_description: str
    polymarket_url: Optional[str]
    polymarket_price: Optional[float]
    consensus_probability: Optional[float]
    consensus_direction: Optional[str]
    divergence_bps: Optional[int]
    freshness_note: Optional[str]


@dataclass
class RunContext:
    run_id: str
    user_id: str
    json_output: bool
    watchlist_limit: int
    consensus_context_limit: int
    min_platforms: int
    min_liquidity_usd: int
    prophet_token: Optional[str]
    seren_api_key: str


# ---------------------------------------------------------------------------
# Argument parsing & config loading
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run prophet-polymarket-edge.")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--command", choices=["run", "status", "purge"], default=None)
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument("--purge", action="store_true", help="Purge audit content for the current user.")
    parser.add_argument("--user-id", default=None)
    parser.add_argument(
        "--yes-live",
        action="store_true",
        help=(
            "Enable live Polymarket execution. Both trading-safety gates "
            "(risk framework, execution path) must pass; otherwise the "
            "process exits 2 with a structured trading_safety_blocked payload."
        ),
    )
    return parser.parse_args(argv)


def load_config(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProphetEdgeError(f"config file is not valid JSON: {exc}") from exc


def resolve_secret(config: dict, name: str) -> Optional[str]:
    secrets = config.get("secrets") if isinstance(config.get("secrets"), dict) else {}
    raw = secrets.get(name)
    if isinstance(raw, str) and raw and not (raw.startswith("${") and raw.endswith("}")):
        return raw
    return os.getenv(name)


# ---------------------------------------------------------------------------
# Storage bootstrap (mirrors prophet-market-seeder pattern)
# ---------------------------------------------------------------------------


def storage_bootstrap_sql(schema_name: str) -> List[str]:
    if not SCHEMA_PATH.exists():
        raise StorageBootstrapError(f"Schema file not found: {SCHEMA_PATH}")
    raw = SCHEMA_PATH.read_text(encoding="utf-8")
    rendered = raw.replace("{{schema_name}}", schema_name)
    # Strip `--` line comments before splitting on `;`. Inline semicolons
    # inside comments would otherwise break the split.
    no_comments = "\n".join(
        line.split("--", 1)[0] if "--" in line else line for line in rendered.splitlines()
    )
    statements = [part.strip() for part in no_comments.split(";") if part.strip()]
    if not statements:
        raise StorageBootstrapError(f"Schema file is empty: {SCHEMA_PATH}")
    return statements


def psycopg_connect(dsn: str):  # pragma: no cover - exercised via tests with monkeypatch
    import psycopg

    return psycopg.connect(dsn)


def apply_storage_bootstrap(connection_string: str, schema_name: str) -> int:
    statements = storage_bootstrap_sql(schema_name)
    try:
        with psycopg_connect(connection_string) as conn:
            with conn.cursor() as cur:
                for statement in statements:
                    cur.execute(statement)
            conn.commit()
    except Exception as exc:
        raise StorageBootstrapError(f"Failed to apply storage bootstrap: {exc}") from exc
    return len(statements)


class SerenApi:
    def __init__(self, api_key: str, api_base: Optional[str] = None):
        if not api_key:
            raise ValueError("SEREN_API_KEY is required")
        self.api_key = api_key
        self.api_base = (api_base or os.getenv("SEREN_API_BASE") or DEFAULT_SEREN_DB_BASE_URL).rstrip("/")

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: Optional[Dict[str, Any]] = None,
        query: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        url = f"{self.api_base}{path}"
        if query:
            url += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})
        req = urllib.request.Request(url=url, method=method)
        req.add_header("Authorization", f"Bearer {self.api_key}")
        req.add_header("Content-Type", "application/json")
        raw = json.dumps(body).encode("utf-8") if body is not None else None
        try:
            with urllib.request.urlopen(req, data=raw, timeout=30) as resp:
                payload = resp.read().decode("utf-8")
        except Exception as exc:
            raise StorageBootstrapError(f"Seren API request failed ({method} {path}): {exc}") from exc
        try:
            return json.loads(payload) if payload else {}
        except json.JSONDecodeError as exc:
            raise StorageBootstrapError(f"Seren API returned invalid JSON for {method} {path}") from exc

    @staticmethod
    def _as_list(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        if isinstance(payload, list):
            return payload
        data = payload.get("data")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("items", "projects", "branches", "databases"):
                items = data.get(key)
                if isinstance(items, list):
                    return items
        return []

    def list_projects(self) -> List[Dict[str, Any]]:
        return self._as_list(self._request("GET", "/projects"))

    def create_project(self, name: str, region: str) -> Dict[str, Any]:
        payload = self._request("POST", "/projects", body={"name": name, "region": region})
        data = payload.get("data")
        return data if isinstance(data, dict) else payload

    def list_branches(self, project_id: str) -> List[Dict[str, Any]]:
        return self._as_list(self._request("GET", f"/projects/{project_id}/branches"))

    def list_databases(self, project_id: str, branch_id: str) -> List[Dict[str, Any]]:
        return self._as_list(self._request("GET", f"/projects/{project_id}/branches/{branch_id}/databases"))

    def create_database(self, project_id: str, branch_id: str, name: str) -> Dict[str, Any]:
        payload = self._request(
            "POST",
            f"/projects/{project_id}/branches/{branch_id}/databases",
            body={"name": name},
        )
        data = payload.get("data")
        return data if isinstance(data, dict) else payload

    def get_connection_string(self, project_id: str, branch_id: str, role: str = "serendb_owner") -> str:
        payload = self._request(
            "GET",
            f"/projects/{project_id}/branches/{branch_id}/connection-string",
            query={"role": role, "pooled": "false"},
        )
        data = payload.get("data")
        if isinstance(data, dict) and data.get("connection_string"):
            return str(data["connection_string"])
        if payload.get("connection_string"):
            return str(payload["connection_string"])
        raise StorageBootstrapError("Could not resolve connection string from Seren API")


def _patch_database(connection_string: str, database_name: str) -> str:
    parsed = urllib.parse.urlparse(connection_string)
    return urllib.parse.urlunparse(
        (parsed.scheme, parsed.netloc, f"/{database_name}", parsed.params, parsed.query, parsed.fragment)
    )


def resolve_or_create_serendb_target(
    api_key: str,
    *,
    project_name: str,
    database_name: str,
    region: str,
) -> SerenDbTarget:
    api = SerenApi(api_key=api_key)
    projects = api.list_projects()
    project = next((p for p in projects if str(p.get("name", "")).lower() == project_name.lower()), None)
    created_project = False
    if not project:
        project = api.create_project(name=project_name, region=region)
        created_project = True
    project_id = str(project.get("id") or "")
    if not project_id:
        raise StorageBootstrapError("Unable to determine project_id for prophet-polymarket-edge")
    branches = api.list_branches(project_id)
    if not branches:
        raise StorageBootstrapError(f"No branches available for project {project_id}")
    default_branch_id = project.get("default_branch_id") if isinstance(project, dict) else None
    branch = None
    if default_branch_id:
        branch = next((b for b in branches if str(b.get("id")) == str(default_branch_id)), None)
    if not branch:
        branch = next((b for b in branches if str(b.get("name", "")).lower() in {"main", "production"}), None)
    if not branch:
        branch = branches[0]
    branch_id = str(branch.get("id") or "")
    branch_name = str(branch.get("name") or "main")
    if not branch_id:
        raise StorageBootstrapError("Unable to determine branch_id for prophet-polymarket-edge")
    databases = api.list_databases(project_id, branch_id)
    db_names = {str(d.get("name")) for d in databases if d.get("name")}
    created_database = False
    if database_name not in db_names:
        api.create_database(project_id=project_id, branch_id=branch_id, name=database_name)
        created_database = True
    conn = _patch_database(api.get_connection_string(project_id=project_id, branch_id=branch_id), database_name)
    return SerenDbTarget(
        project_id=project_id,
        branch_id=branch_id,
        database_name=database_name,
        connection_string=conn,
        project_name=str(project.get("name") or project_name),
        branch_name=branch_name,
        created_project=created_project,
        created_database=created_database,
    )


def ensure_storage(config: dict) -> dict:
    storage_cfg = config.get("storage") if isinstance(config.get("storage"), dict) else {}
    project_name = str(storage_cfg.get("project_name") or DEFAULT_PROJECT_NAME)
    database_name = str(storage_cfg.get("database_name") or DEFAULT_DATABASE_NAME)
    schema_name = str(storage_cfg.get("schema_name") or DEFAULT_SCHEMA_NAME)
    region = str(storage_cfg.get("region") or DEFAULT_REGION)
    connection_string = storage_cfg.get("connection_string") or os.getenv("SERENDB_URL")
    api_key = resolve_secret(config, "SEREN_API_KEY")

    target: Optional[SerenDbTarget] = None
    if not connection_string:
        if not api_key:
            raise StorageBootstrapError(
                "SEREN_API_KEY is required to auto-provision prophet-polymarket-edge storage. "
                f"See {SEREN_SKILLS_DOCS_URL}."
            )
        target = resolve_or_create_serendb_target(
            api_key,
            project_name=project_name,
            database_name=database_name,
            region=region,
        )
        connection_string = target.connection_string

    executed = apply_storage_bootstrap(connection_string, schema_name)
    result = {
        "status": "ok",
        "schema_name": schema_name,
        "database_name": database_name,
        "project_name": project_name,
        "statements_executed": executed,
        "connection_string": connection_string,
    }
    if target:
        result.update(
            {
                "project_id": target.project_id,
                "branch_id": target.branch_id,
                "branch_name": target.branch_name,
                "created_project": bool(target.created_project),
                "created_database": bool(target.created_database),
            }
        )
    return result


# ---------------------------------------------------------------------------
# Prophet GraphQL — open markets only (no execution)
# ---------------------------------------------------------------------------


class ProphetApi:
    """Read-only Prophet GraphQL client for open-markets enumeration.

    The token is optional. If absent, callers should treat the open-market
    list as empty and surface deep-link suppression to the user.
    """

    def __init__(self, session_token: Optional[str], base_url: Optional[str] = None):
        self.session_token = session_token
        self.base_url = (base_url or os.getenv("PROPHET_BASE_URL") or DEFAULT_PROPHET_BASE_URL).rstrip("/")

    def fetch_open_markets(self, limit: int = 100) -> List[Dict[str, Any]]:
        if not self.session_token:
            return []
        url = f"{self.base_url}/api/graphql"
        body = {
            "query": OPEN_MARKETS_QUERY,
            "operationName": "OpenMarkets",
            "variables": {"limit": limit},
        }
        req = urllib.request.Request(
            url=url,
            method="POST",
            data=json.dumps(body).encode("utf-8"),
        )
        req.add_header("Authorization", f"Bearer {self.session_token}")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode("utf-8") or "{}")
        except Exception as exc:
            raise ProphetGraphQLError(f"Prophet open-markets query failed: {exc}") from exc
        errors = payload.get("errors")
        if errors:
            raise ProphetGraphQLError(f"Prophet GraphQL error: {errors[0].get('message', errors)}")
        data = payload.get("data") or {}
        markets = data.get("openMarkets") or []
        return markets if isinstance(markets, list) else []


# ---------------------------------------------------------------------------
# Polymarket intelligence — divergence + consensus only (NOT actionable)
# ---------------------------------------------------------------------------


class PolymarketIntelligence:
    """Read-only client for the seren-polymarket-intelligence publisher.

    This client deliberately does NOT expose `/api/oracle/actionable`.
    That endpoint is a recommendation engine and is out of scope at v1.
    """

    def __init__(self, api_key: str, base_url: Optional[str] = None):
        if not api_key:
            raise ValueError("SEREN_API_KEY is required for Polymarket intelligence")
        self.api_key = api_key
        self.base_url = (base_url or os.getenv("POLYMARKET_INTEL_BASE_URL") or DEFAULT_INTEL_BASE_URL).rstrip("/")

    def _get(self, path: str, query: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        if query:
            url += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})
        req = urllib.request.Request(url=url, method="GET")
        req.add_header("Authorization", f"Bearer {self.api_key}")
        req.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = resp.read().decode("utf-8")
        except Exception as exc:
            raise IntelligenceError(f"Polymarket intelligence GET {path} failed: {exc}") from exc
        try:
            return json.loads(payload) if payload else {}
        except json.JSONDecodeError as exc:
            raise IntelligenceError(f"Polymarket intelligence returned invalid JSON for {path}") from exc

    def divergence(self, *, min_platforms: int, min_liquidity_usd: int) -> List[Dict[str, Any]]:
        payload = self._get(
            "/api/oracle/divergence",
            {"min_platforms": min_platforms, "min_liquidity_usd": min_liquidity_usd},
        )
        items = payload.get("data") if isinstance(payload, dict) else payload
        if isinstance(items, dict):
            items = items.get("items") or items.get("markets") or []
        return items if isinstance(items, list) else []

    def consensus_batch(self, canonical_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        if not canonical_ids:
            return {}
        payload = self._get(
            "/api/oracle/consensus/batch",
            {"ids": ",".join(canonical_ids)},
        )
        data = payload.get("data") if isinstance(payload, dict) else payload
        if isinstance(data, list):
            return {str(item.get("canonical_id") or item.get("id")): item for item in data if isinstance(item, dict)}
        if isinstance(data, dict):
            return {str(k): v for k, v in data.items() if isinstance(v, dict)}
        return {}


# ---------------------------------------------------------------------------
# Pure transforms (testable without network or DB)
# ---------------------------------------------------------------------------


def normalize_prophet_open_titles(markets: List[Dict[str, Any]]) -> List[str]:
    """Lowercased, whitespace-collapsed titles of currently-open Prophet markets."""
    out: List[str] = []
    for m in markets or []:
        title = (m.get("title") or m.get("question") or "").strip().lower()
        if title:
            out.append(" ".join(title.split()))
    return out


def _market_title_key(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def compute_watchlist_candidates(
    *,
    divergence_rows: List[Dict[str, Any]],
    consensus_by_id: Dict[str, Dict[str, Any]],
    prophet_open_titles: List[str],
    watchlist_limit: int,
    min_platforms: int,
    min_liquidity_usd: int,
) -> List[WatchlistCandidate]:
    """Compute the Surface B watchlist.

    Set-difference: keep Polymarket divergence rows whose market description
    is not already an open Prophet market. Rank by (platform_count desc,
    divergence_bps desc, liquidity desc).
    """
    open_keys = set(prophet_open_titles)
    out: List[WatchlistCandidate] = []
    for row in divergence_rows or []:
        if not isinstance(row, dict):
            continue
        canonical_id = str(row.get("canonical_id") or row.get("id") or "")
        if not canonical_id:
            continue
        description = str(row.get("description") or row.get("title") or row.get("question") or "").strip()
        if not description:
            continue
        if _market_title_key(description) in open_keys:
            continue
        platform_count = row.get("platform_count")
        if isinstance(platform_count, (int, float)) and platform_count < min_platforms:
            continue
        liquidity = row.get("liquidity_usd")
        if isinstance(liquidity, (int, float)) and liquidity < min_liquidity_usd:
            continue
        consensus = consensus_by_id.get(canonical_id) or {}
        consensus_prob = consensus.get("consensus_probability")
        consensus_dir = consensus.get("consensus_direction") or row.get("consensus_direction")
        polymarket_price = row.get("polymarket_price") or consensus.get("polymarket_price")
        polymarket_url = row.get("polymarket_url") or consensus.get("polymarket_url")
        divergence_bps = row.get("divergence_bps")
        confidence = consensus.get("confidence") or row.get("confidence")
        why_listed_bits = []
        if isinstance(platform_count, (int, float)) and platform_count >= min_platforms:
            why_listed_bits.append(f"divergent across {int(platform_count)} consensus venues")
        if isinstance(divergence_bps, (int, float)) and divergence_bps:
            why_listed_bits.append(f"divergence {int(divergence_bps)} bps")
        if isinstance(liquidity, (int, float)) and liquidity >= min_liquidity_usd:
            why_listed_bits.append(f"Polymarket liquidity ${int(liquidity):,}")
        why_listed = "; ".join(why_listed_bits) or "long-tail event not yet on Prophet"
        out.append(
            WatchlistCandidate(
                canonical_id=canonical_id,
                description=description,
                resolution_criteria=row.get("resolution_criteria"),
                consensus_probability=_as_float(consensus_prob),
                consensus_direction=str(consensus_dir) if consensus_dir is not None else None,
                divergence_bps=_as_int(divergence_bps),
                polymarket_price=_as_float(polymarket_price),
                polymarket_url=str(polymarket_url) if polymarket_url else None,
                why_listed=why_listed,
                confidence=str(confidence) if confidence else None,
                liquidity_usd=_as_float(liquidity),
                platform_count=_as_int(platform_count),
            )
        )

    def _sort_key(c: WatchlistCandidate) -> Tuple[int, int, int]:
        return (
            -(c.platform_count or 0),
            -(abs(c.divergence_bps) if c.divergence_bps is not None else 0),
            -int(c.liquidity_usd or 0),
        )

    out.sort(key=_sort_key)
    return out[:watchlist_limit]


def compute_consensus_context_rows(
    *,
    divergence_rows: List[Dict[str, Any]],
    consensus_by_id: Dict[str, Dict[str, Any]],
    consensus_context_limit: int,
) -> List[ConsensusContextRow]:
    out: List[ConsensusContextRow] = []
    for row in divergence_rows or []:
        if not isinstance(row, dict):
            continue
        canonical_id = str(row.get("canonical_id") or row.get("id") or "")
        if not canonical_id:
            continue
        consensus = consensus_by_id.get(canonical_id) or {}
        out.append(
            ConsensusContextRow(
                canonical_id=canonical_id,
                market_description=str(row.get("description") or row.get("title") or "").strip(),
                polymarket_url=row.get("polymarket_url") or consensus.get("polymarket_url"),
                polymarket_price=_as_float(row.get("polymarket_price") or consensus.get("polymarket_price")),
                consensus_probability=_as_float(consensus.get("consensus_probability")),
                consensus_direction=(
                    str(consensus.get("consensus_direction"))
                    if consensus.get("consensus_direction") is not None
                    else (str(row.get("consensus_direction")) if row.get("consensus_direction") is not None else None)
                ),
                divergence_bps=_as_int(row.get("divergence_bps")),
                freshness_note=consensus.get("freshness_note") or consensus.get("freshness"),
            )
        )
    return out[:consensus_context_limit]


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def render_consensus_context_label() -> str:
    """Return the verbatim §6.1 consensus-context block."""
    return CONSENSUS_CONTEXT_BLOCK


def render_watchlist(
    *,
    candidates: List[WatchlistCandidate],
    prophet_authenticated: bool,
) -> str:
    """Render the Surface B watchlist.

    The prophet_authenticated flag controls deep-link rendering per the
    Surface B auth split (final v3 audit P1-3): the watchlist itself
    renders unauthenticated, but the "Create this market on Prophet"
    deep link requires Prophet auth.
    """
    lines: List[str] = []
    lines.append("Tranche 1 watchlist — Prophet markets not yet open")
    lines.append("=" * 60)
    if not candidates:
        lines.append("No candidate markets passed the divergence + liquidity filters.")
        return "\n".join(lines)
    for idx, c in enumerate(candidates, start=1):
        lines.append("")
        lines.append(f"{idx}. {c.description}")
        if c.resolution_criteria:
            lines.append(f"   Resolution: {c.resolution_criteria}")
        lines.append(f"   Why listed: {c.why_listed}")
        lines.append("")
        lines.append("   " + CONSENSUS_CONTEXT_BLOCK.replace("\n", "\n   "))
        if c.consensus_probability is not None:
            lines.append(f"   consensus probability: {c.consensus_probability:.3f}")
        if c.consensus_direction:
            lines.append(f"   consensus direction: {c.consensus_direction}")
        if c.divergence_bps is not None:
            lines.append(f"   divergence: {c.divergence_bps} bps")
        if prophet_authenticated:
            lines.append(f"   [Create this market on Prophet]  (canonical id: {c.canonical_id})")
        else:
            lines.append("   (deep link suppressed — Prophet auth not provided; sign in to create the market)")
    return "\n".join(lines)


def render_consensus_context(rows: List[ConsensusContextRow]) -> str:
    """Render Surface C with the verbatim labels.

    Uses 'consensus probability' and 'consensus direction' — never the
    forbidden 'recommended side' phrasing (§6.2).
    """
    lines: List[str] = []
    lines.append("Polymarket consensus context (read-only)")
    lines.append("=" * 60)
    if not rows:
        lines.append("No divergent markets matched the filters.")
        return "\n".join(lines)
    for idx, r in enumerate(rows, start=1):
        lines.append("")
        lines.append(f"{idx}. {r.market_description or r.canonical_id}")
        if r.polymarket_url:
            lines.append(f"   Polymarket: {r.polymarket_url}")
        if r.polymarket_price is not None:
            lines.append(f"   current Polymarket price: {r.polymarket_price:.3f}")
        if r.consensus_probability is not None:
            lines.append(f"   consensus probability: {r.consensus_probability:.3f}")
        if r.consensus_direction:
            lines.append(f"   consensus direction: {r.consensus_direction}")
        if r.divergence_bps is not None:
            lines.append(f"   divergence: {r.divergence_bps} bps")
        if r.freshness_note:
            lines.append(f"   freshness: {r.freshness_note}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Telemetry + audit-run + recommendation persistence
# ---------------------------------------------------------------------------


def write_telemetry_event(
    *,
    connection_string: str,
    schema_name: str,
    user_id: Optional[str],
    audit_run_id: Optional[int],
    event_type: str,
    payload: Optional[dict] = None,
) -> None:
    with psycopg_connect(connection_string) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {schema_name}.telemetry_events
                  (user_id, audit_run_id, event_type, event_payload)
                VALUES (%s, %s, %s, %s::jsonb)
                """,
                (user_id, audit_run_id, event_type, json.dumps(payload or {})),
            )
        conn.commit()


def insert_audit_run(
    *,
    connection_string: str,
    schema_name: str,
    user_id: str,
    surfaces: List[str],
    status: str,
) -> int:
    with psycopg_connect(connection_string) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {schema_name}.audit_runs
                  (user_id, surfaces_invoked, status)
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                (user_id, surfaces, status),
            )
            row = cur.fetchone()
            audit_run_id = int(row[0]) if row else 0
        conn.commit()
    return audit_run_id


def insert_recommendations(
    *,
    connection_string: str,
    schema_name: str,
    audit_run_id: int,
    surface_b: List[WatchlistCandidate],
    surface_c: List[ConsensusContextRow],
) -> int:
    inserted = 0
    with psycopg_connect(connection_string) as conn:
        with conn.cursor() as cur:
            for rank, c in enumerate(surface_b, start=1):
                cur.execute(
                    f"""
                    INSERT INTO {schema_name}.recommendations
                      (audit_run_id, surface, rank, source, market_description, market_url,
                       suggested_side, consensus_probability, current_market_price,
                       divergence_bps, rationale)
                    VALUES (%s, 'B_tranche1', %s, 'prophet_create', %s, %s, 'none',
                            %s, %s, %s, %s)
                    """,
                    (
                        audit_run_id,
                        rank,
                        c.description,
                        c.polymarket_url,
                        c.consensus_probability,
                        c.polymarket_price,
                        c.divergence_bps,
                        c.why_listed,
                    ),
                )
                inserted += 1
            for rank, r in enumerate(surface_c, start=1):
                cur.execute(
                    f"""
                    INSERT INTO {schema_name}.recommendations
                      (audit_run_id, surface, rank, source, market_description, market_url,
                       suggested_side, consensus_probability, current_market_price,
                       divergence_bps, rationale)
                    VALUES (%s, 'C_polymarket', %s, 'polymarket_existing', %s, %s, 'none',
                            %s, %s, %s, %s)
                    """,
                    (
                        audit_run_id,
                        rank,
                        r.market_description,
                        r.polymarket_url,
                        r.consensus_probability,
                        r.polymarket_price,
                        r.divergence_bps,
                        r.freshness_note or "",
                    ),
                )
                inserted += 1
        conn.commit()
    return inserted


# ---------------------------------------------------------------------------
# Purge path (§10.4 / §13.19)
# ---------------------------------------------------------------------------


def purge_user_audit_content(
    *,
    connection_string: str,
    schema_name: str,
    user_id: str,
) -> dict:
    """Delete all audit content for a user.

    Removes audit_runs, audit_findings, recommendations,
    cost_estimate_gates, telemetry_events, and wallet_identities.
    """
    deleted = {
        "audit_runs": 0,
        "audit_findings": 0,
        "recommendations": 0,
        "cost_estimate_gates": 0,
        "telemetry_events": 0,
        "wallet_identities": 0,
    }
    with psycopg_connect(connection_string) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT id FROM {schema_name}.audit_runs WHERE user_id = %s",
                (user_id,),
            )
            run_ids = [r[0] for r in cur.fetchall()]
            if run_ids:
                cur.execute(
                    f"DELETE FROM {schema_name}.audit_findings WHERE audit_run_id = ANY(%s)",
                    (run_ids,),
                )
                deleted["audit_findings"] = cur.rowcount
                cur.execute(
                    f"DELETE FROM {schema_name}.recommendations WHERE audit_run_id = ANY(%s)",
                    (run_ids,),
                )
                deleted["recommendations"] = cur.rowcount
                cur.execute(
                    f"DELETE FROM {schema_name}.cost_estimate_gates WHERE audit_run_id = ANY(%s)",
                    (run_ids,),
                )
                deleted["cost_estimate_gates"] = cur.rowcount
                cur.execute(
                    f"DELETE FROM {schema_name}.telemetry_events WHERE audit_run_id = ANY(%s)",
                    (run_ids,),
                )
                deleted["telemetry_events"] = cur.rowcount
            cur.execute(
                f"DELETE FROM {schema_name}.audit_runs WHERE user_id = %s",
                (user_id,),
            )
            deleted["audit_runs"] = cur.rowcount
            cur.execute(
                f"DELETE FROM {schema_name}.wallet_identities WHERE user_id = %s",
                (user_id,),
            )
            deleted["wallet_identities"] = cur.rowcount
        conn.commit()
    return deleted


# ---------------------------------------------------------------------------
# Top-level run orchestration
# ---------------------------------------------------------------------------


def execute_run(
    *,
    config: dict,
    args: argparse.Namespace,
    storage_result: dict,
    user_id: str,
) -> dict:
    """Run the Surface B + Surface C pipeline."""
    inputs = config.get("inputs") if isinstance(config.get("inputs"), dict) else {}
    watchlist_limit = int(inputs.get("watchlist_limit") or 5)
    consensus_context_limit = int(inputs.get("consensus_context_limit") or 10)
    min_platforms = int(inputs.get("min_platforms") or 3)
    min_liquidity_usd = int(inputs.get("min_liquidity_usd") or 10000)
    json_output = bool(args.json_output or inputs.get("json_output"))

    api_key = resolve_secret(config, "SEREN_API_KEY")
    if not api_key:
        raise StorageBootstrapError(
            f"SEREN_API_KEY is required. See {SEREN_SKILLS_DOCS_URL}."
        )
    prophet_token = resolve_secret(config, "PROPHET_SESSION_TOKEN")

    schema_name = storage_result["schema_name"]
    connection_string = storage_result["connection_string"]

    audit_run_id = insert_audit_run(
        connection_string=connection_string,
        schema_name=schema_name,
        user_id=user_id,
        surfaces=["B_tranche1", "C_polymarket"],
        status="running",
    )

    # Pull data
    intel = PolymarketIntelligence(api_key=api_key)
    divergence_rows = intel.divergence(
        min_platforms=min_platforms,
        min_liquidity_usd=min_liquidity_usd,
    )
    canonical_ids = [str(r.get("canonical_id") or r.get("id")) for r in divergence_rows if isinstance(r, dict)]
    canonical_ids = [c for c in canonical_ids if c]
    consensus_by_id = intel.consensus_batch(canonical_ids)

    prophet_api = ProphetApi(session_token=prophet_token)
    open_markets = prophet_api.fetch_open_markets(limit=200)
    open_titles = normalize_prophet_open_titles(open_markets)

    # Surface B
    surface_b = compute_watchlist_candidates(
        divergence_rows=divergence_rows,
        consensus_by_id=consensus_by_id,
        prophet_open_titles=open_titles,
        watchlist_limit=watchlist_limit,
        min_platforms=min_platforms,
        min_liquidity_usd=min_liquidity_usd,
    )

    surface_b_text = render_watchlist(
        candidates=surface_b,
        prophet_authenticated=bool(prophet_token),
    )

    # Surface C
    surface_c = compute_consensus_context_rows(
        divergence_rows=divergence_rows,
        consensus_by_id=consensus_by_id,
        consensus_context_limit=consensus_context_limit,
    )
    surface_c_text = render_consensus_context(surface_c)

    # Persist
    n_recs = insert_recommendations(
        connection_string=connection_string,
        schema_name=schema_name,
        audit_run_id=audit_run_id,
        surface_b=surface_b,
        surface_c=surface_c,
    )
    write_telemetry_event(
        connection_string=connection_string,
        schema_name=schema_name,
        user_id=user_id,
        audit_run_id=audit_run_id,
        event_type="run_completed",
        payload={
            "watchlist_count": len(surface_b),
            "consensus_context_count": len(surface_c),
            "prophet_authenticated": bool(prophet_token),
        },
    )

    return {
        "status": "ok",
        "audit_run_id": audit_run_id,
        "watchlist": [_candidate_to_dict(c) for c in surface_b],
        "consensus_context": [_consensus_to_dict(r) for r in surface_c],
        "watchlist_text": surface_b_text,
        "consensus_context_text": surface_c_text,
        "recommendations_persisted": n_recs,
        "prophet_authenticated": bool(prophet_token),
        "json_output": json_output,
    }


def _candidate_to_dict(c: WatchlistCandidate) -> dict:
    return {
        "canonical_id": c.canonical_id,
        "description": c.description,
        "resolution_criteria": c.resolution_criteria,
        "consensus_probability": c.consensus_probability,
        "consensus_direction": c.consensus_direction,
        "divergence_bps": c.divergence_bps,
        "polymarket_price": c.polymarket_price,
        "polymarket_url": c.polymarket_url,
        "why_listed": c.why_listed,
        "confidence": c.confidence,
        "liquidity_usd": c.liquidity_usd,
        "platform_count": c.platform_count,
    }


def _consensus_to_dict(r: ConsensusContextRow) -> dict:
    return {
        "canonical_id": r.canonical_id,
        "market_description": r.market_description,
        "polymarket_url": r.polymarket_url,
        "polymarket_price": r.polymarket_price,
        "consensus_probability": r.consensus_probability,
        "consensus_direction": r.consensus_direction,
        "divergence_bps": r.divergence_bps,
        "freshness_note": r.freshness_note,
    }


# ---------------------------------------------------------------------------
# Live execution path (--yes-live)
#
# Lifted from polymarket/maker-rebate-bot/scripts/polymarket_live.py:2049-2187.
# `py_clob_client` is imported lazily so the read-only path does not require it.
# ---------------------------------------------------------------------------


DEFAULT_POLY_CHAIN_ID = 137
DEFAULT_POLY_CLOB_HOST = "https://clob.polymarket.com"


class DirectClobTrader:
    """Direct Polymarket CLOB client for local py-clob-client execution."""

    def __init__(self) -> None:
        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import ApiCreds
        except ImportError as exc:
            raise RuntimeError(
                "Direct CLOB trading requires `py-clob-client`. "
                "Install with `python -m pip install -r requirements.txt`."
            ) from exc

        private_key = (os.getenv("POLY_PRIVATE_KEY") or os.getenv("WALLET_PRIVATE_KEY") or "").strip()
        api_key = (os.getenv("POLY_API_KEY") or "").strip()
        api_passphrase = (os.getenv("POLY_PASSPHRASE") or "").strip()
        api_secret = (os.getenv("POLY_SECRET") or "").strip()
        if not private_key:
            raise RuntimeError(
                "Direct CLOB trading requires `POLY_PRIVATE_KEY` or `WALLET_PRIVATE_KEY`."
            )
        if not api_key or not api_passphrase or not api_secret:
            raise RuntimeError(
                "Missing required Polymarket L2 credentials. Set "
                "`POLY_API_KEY`, `POLY_PASSPHRASE`, and `POLY_SECRET`."
            )

        chain_id = _as_int(os.getenv("POLY_CHAIN_ID")) or DEFAULT_POLY_CHAIN_ID
        creds = ApiCreds(api_key=api_key, api_secret=api_secret, api_passphrase=api_passphrase)
        self._client = ClobClient(
            host=DEFAULT_POLY_CLOB_HOST,
            key=private_key,
            chain_id=chain_id,
            creds=creds,
        )

    def create_order(self, *, token_id: str, side: str, price: float, size: float) -> Any:
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY, SELL

        clob_side = BUY if side.upper() == "BUY" else SELL
        order_args = OrderArgs(price=price, size=size, side=clob_side, token_id=token_id)
        signed_order = self._client.create_order(order_args)
        return self._client.post_order(signed_order, OrderType.GTC)

    def get_cash_balance(self) -> float:
        from py_clob_client.clob_types import AssetType, BalanceAllowanceParams

        payload = self._client.get_balance_allowance(
            BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        )
        # USDC has 6 decimals on Polymarket; collapse to dollar units.
        raw = float(payload.get("balance") or payload.get("amount") or 0.0)
        if raw > 1_000_000:
            return raw / 1_000_000.0
        return raw


def _kelly_fraction_yes(*, market_price: float, win_prob: float) -> float:
    """Standard Kelly for a binary contract priced at `market_price` with
    estimated win probability `win_prob`. Returns 0 when there is no edge.
    """
    if not (0.0 < market_price < 1.0):
        return 0.0
    b = (1.0 - market_price) / market_price
    if b <= 0.0:
        return 0.0
    q = 1.0 - win_prob
    f_star = (b * win_prob - q) / b
    return max(0.0, f_star)


def compute_live_executions(
    *,
    surface_c: List[Dict[str, Any]],
    risk: Dict[str, Any],
    bankroll_usd: float,
    min_divergence_bps: int,
) -> List[Dict[str, Any]]:
    """Plan BUY orders on the consensus side for divergent Surface C rows.

    Returns one plan per tradable row. Skips silently when:
      - `consensus_direction` is missing
      - `polymarket_token_id` is missing
      - `divergence_bps` is below the configured floor
      - Kelly edge is non-positive
    """
    plans: List[Dict[str, Any]] = []
    kelly_cap = max(0.0, _safe_float_local(risk.get("max_kelly_fraction"))) if risk else 0.0
    per_market_cap_usd = max(0.0, _safe_float_local(risk.get("max_position_notional_usd"))) if risk else 0.0
    if kelly_cap <= 0.0 or per_market_cap_usd <= 0.0 or bankroll_usd <= 0.0:
        return plans

    for row in surface_c or []:
        if not isinstance(row, dict):
            continue
        token_id = row.get("polymarket_token_id")
        if not token_id:
            continue
        direction = row.get("consensus_direction")
        if direction not in ("yes", "no"):
            continue
        divergence_bps = _safe_float_local(row.get("divergence_bps"))
        if divergence_bps < float(min_divergence_bps):
            continue
        polymarket_price = _safe_float_local(row.get("polymarket_price"))
        consensus_prob = _safe_float_local(row.get("consensus_probability"))
        if not (0.0 < polymarket_price < 1.0) or not (0.0 < consensus_prob < 1.0):
            continue

        # The token's effective entry price + win probability depend on
        # which side we're buying. `polymarket_token_id` is the consensus
        # side: YES side carries `polymarket_price`/`consensus_probability`;
        # NO side carries the complements.
        if direction == "yes":
            entry_price = polymarket_price
            win_prob = consensus_prob
        else:
            entry_price = 1.0 - polymarket_price
            win_prob = 1.0 - consensus_prob

        f_star = _kelly_fraction_yes(market_price=entry_price, win_prob=win_prob)
        if f_star <= 0.0:
            continue
        fraction = min(f_star, kelly_cap)
        notional_usd = min(bankroll_usd * fraction, per_market_cap_usd)
        if notional_usd <= 0.0 or entry_price <= 0.0:
            continue
        size_shares = round(notional_usd / entry_price, 4)
        plans.append(
            {
                "canonical_id": str(row.get("canonical_id") or ""),
                "token_id": str(token_id),
                "side": "BUY",
                "consensus_direction": direction,
                "limit_price": round(entry_price, 4),
                "size_shares": size_shares,
                "notional_usd": round(notional_usd, 4),
                "kelly_fraction": round(fraction, 6),
                "divergence_bps": int(divergence_bps),
            }
        )
    return plans


def _safe_float_local(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def fetch_surface_c_for_live_execution(config: dict) -> List[Dict[str, Any]]:
    """Fetch the Surface C divergence + consensus rows the live path trades on.

    Mirrors the read-only `execute_run` Surface C step but does not persist.
    Returns dicts shaped like `_consensus_to_dict` plus `polymarket_token_id`
    when the divergence row carries it.
    """
    inputs = config.get("inputs") if isinstance(config.get("inputs"), dict) else {}
    min_platforms = int(inputs.get("min_platforms") or 3)
    min_liquidity_usd = int(inputs.get("min_liquidity_usd") or 10000)
    consensus_context_limit = int(inputs.get("consensus_context_limit") or 10)
    api_key = resolve_secret(config, "SEREN_API_KEY")
    if not api_key:
        return []
    intel = PolymarketIntelligence(api_key=api_key)
    divergence_rows = intel.divergence(min_platforms=min_platforms, min_liquidity_usd=min_liquidity_usd)
    canonical_ids = [str(r.get("canonical_id") or r.get("id") or "") for r in divergence_rows if isinstance(r, dict)]
    canonical_ids = [c for c in canonical_ids if c]
    consensus_by_id = intel.consensus_batch(canonical_ids)
    rows = compute_consensus_context_rows(
        divergence_rows=divergence_rows,
        consensus_by_id=consensus_by_id,
        consensus_context_limit=consensus_context_limit,
    )
    out: List[Dict[str, Any]] = []
    for row, raw in zip(rows, divergence_rows[: len(rows)]):
        d = _consensus_to_dict(row)
        token_id = (
            (raw.get("polymarket_token_id") if isinstance(raw, dict) else None)
            or (consensus_by_id.get(d["canonical_id"], {}).get("polymarket_token_id"))
        )
        d["polymarket_token_id"] = token_id
        out.append(d)
    return out


def _yes_live_main(args: argparse.Namespace) -> int:
    """Live execution path. Run gates → fetch Surface C → submit orders."""
    import importlib.util as _ts_util

    _ts_path = Path(__file__).resolve().parent / "trading_safety.py"
    _ts_spec = _ts_util.spec_from_file_location("trading_safety", _ts_path)
    assert _ts_spec is not None and _ts_spec.loader is not None
    _ts_mod = _ts_util.module_from_spec(_ts_spec)
    sys.modules["trading_safety"] = _ts_mod
    _ts_spec.loader.exec_module(_ts_mod)

    config = load_config(args.config)
    payload = _ts_mod.evaluate_trading_safety_gates(config)
    if not payload.get("passed"):
        sys.stderr.write(
            "ERROR: --yes-live is blocked by the trading-safety gates. "
            "See `trading_safety_blocked` payload below for the remediation list.\n"
        )
        sys.stderr.write(json.dumps(payload, sort_keys=True) + "\n")
        return 2

    live_cfg = config.get("live") if isinstance(config.get("live"), dict) else {}
    risk = config.get("risk") if isinstance(config.get("risk"), dict) else {}
    bankroll_usd = _safe_float_local(live_cfg.get("bankroll_usd"))
    min_divergence_bps = int(live_cfg.get("min_divergence_bps") or 500)

    surface_c = fetch_surface_c_for_live_execution(config)
    plans = compute_live_executions(
        surface_c=surface_c,
        risk=risk,
        bankroll_usd=bankroll_usd,
        min_divergence_bps=min_divergence_bps,
    )

    executions: List[Dict[str, Any]] = []
    if plans:
        trader = DirectClobTrader()
        for plan in plans:
            try:
                response = trader.create_order(
                    token_id=plan["token_id"],
                    side=plan["side"],
                    price=plan["limit_price"],
                    size=plan["size_shares"],
                )
                executions.append({**plan, "status": "submitted", "broker_response": response})
            except Exception as exc:
                executions.append({**plan, "status": "error", "error": str(exc)})

    print(
        json.dumps(
            {
                "status": "ok",
                "trading_safety": payload,
                "plans": plans,
                "live_executions": executions,
            },
            default=str,
        )
    )
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    if args.yes_live:
        return _yes_live_main(args)

    config = load_config(args.config)
    command = args.command or (config.get("inputs") or {}).get("command") or "run"
    user_id = args.user_id or os.getenv("SEREN_USER_ID") or "local-dev"

    try:
        storage_result = ensure_storage(config)
    except StorageBootstrapError as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return 1

    if command == "purge" or args.purge:
        deleted = purge_user_audit_content(
            connection_string=storage_result["connection_string"],
            schema_name=storage_result["schema_name"],
            user_id=user_id,
        )
        result = {"status": "purged", "deleted": deleted}
    elif command == "status":
        result = {"status": "ok", "storage": storage_result}
    elif command == "run":
        try:
            result = execute_run(
                config=config,
                args=args,
                storage_result=storage_result,
                user_id=user_id,
            )
        except (IntelligenceError, ProphetGraphQLError) as exc:
            sys.stderr.write(f"ERROR: {exc}\n")
            return 3
    else:
        sys.stderr.write(f"ERROR: unknown command {command!r}\n")
        return 2

    if result.get("json_output"):
        print(json.dumps(result, default=str))
    else:
        if "watchlist_text" in result:
            print(result["watchlist_text"])
            print()
            print(result["consensus_context_text"])
        else:
            print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
