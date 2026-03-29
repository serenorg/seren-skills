#!/usr/bin/env python3
"""Runtime for prophet-market-seeder with explicit Prophet auth and storage bootstrap."""

from __future__ import annotations

import argparse
import json
import os
import sys

# --- Force unbuffered stdout so piped/background output is visible immediately ---
if not sys.stdout.isatty():
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
# --- End unbuffered stdout fix ---

import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


DEFAULT_DRY_RUN = False
DEFAULT_COMMAND = "run"
DEFAULT_PROJECT_NAME = "prophet"
DEFAULT_DATABASE_NAME = "prophet"
DEFAULT_SCHEMA_NAME = "prophet_market_seeder"
DEFAULT_REGION = "aws-us-east-2"
DEFAULT_PROPHET_BASE_URL = "https://app.prophetmarket.ai"
DEFAULT_PROPHET_TESTNET_BASE_URL = "https://testnet.prophetmarket.ai"
PROPHET_TESTNET_USDC_FAUCET = "0xa0f2da5e260486895d73086dd98af09c25dc2883c6ac96025a688f855c180d06"
SEREN_SKILLS_DOCS_URL = "https://docs.serendb.com/skills.md"
AVAILABLE_CONNECTORS = ["storage"]
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "serendb_schema.sql"
VIEWER_WALLET_BALANCE_QUERY = """
query ViewerWalletBalance {
  viewer {
    walletBalance {
      availableCents
      totalCents
      safeAddress
      safeDeployed
      __typename
    }
    __typename
  }
}
""".strip()

INITIATE_MARKET_MUTATION = """
mutation InitiateMarket($input: InitiateMarketInput!) {
  initiateMarket(input: $input) {
    isValid
    suggestion
    title
    resolutionDate
    resolutionRules
  }
}
""".strip()

MARKET_CATEGORIES = [
    "Politics", "Sports", "Economics", "Financials", "Crypto",
    "Climate", "Culture", "Companies", "Tech & Science", "Health", "World",
]

CANDIDATE_TEMPLATES = [
    ("Politics", "Will the US pass new {topic} legislation by {date}?"),
    ("Politics", "Will the {office} approval rating exceed 50% by {date}?"),
    ("Economics", "Will US GDP growth exceed {pct}% in Q{quarter} {year}?"),
    ("Economics", "Will the Federal Reserve cut interest rates by {date}?"),
    ("Financials", "Will the S&P 500 close above {level} by {date}?"),
    ("Financials", "Will Bitcoin ETF daily inflows exceed ${amount}M by {date}?"),
    ("Crypto", "Will Bitcoin price exceed ${btc_price}K by {date}?"),
    ("Crypto", "Will Ethereum price exceed ${eth_price}K by {date}?"),
    ("Crypto", "Will total crypto market cap exceed ${mcap}T by {date}?"),
    ("Tech & Science", "Will {company} announce a major AI product by {date}?"),
    ("Tech & Science", "Will a new AI model surpass GPT-4 benchmarks by {date}?"),
    ("Sports", "Will {team} win the {league} championship in {year}?"),
    ("Health", "Will the WHO declare a new public health emergency by {date}?"),
    ("Climate", "Will global average temperature in {year} set a new record?"),
    ("Companies", "Will {company} stock price exceed ${price} by {date}?"),
    ("Culture", "Will {movie_or_show} win Best Picture at the {year} Oscars?"),
    ("World", "Will a new international trade agreement be signed by {date}?"),
]


class ProphetSkillError(RuntimeError):
    """Base error for runtime failures."""


class ProphetAuthError(ProphetSkillError):
    """Raised when Prophet auth cannot be validated."""


class SerenBootstrapError(ProphetSkillError):
    """Raised when Seren storage bootstrap fails."""


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run prophet-market-seeder.")
    parser.add_argument(
        "--config",
        default="config.json",
        help="Path to runtime config file (default: config.json).",
    )
    return parser.parse_args()


def _bootstrap_config_path(config_path: str) -> Path:
    path = Path(config_path)
    if path.exists():
        return path
    example_path = path.with_name("config.example.json")
    if example_path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(example_path.read_text(encoding="utf-8"), encoding="utf-8")
    return path


def load_config(config_path: str) -> dict:
    path = _bootstrap_config_path(config_path)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _normalize_command(value: Any) -> str:
    command = str(value or DEFAULT_COMMAND).strip().lower()
    if command not in {"setup", "run", "status"}:
        raise ProphetSkillError(f"Unsupported command: {command}")
    return command


def _config_inputs(config: dict) -> dict:
    return config.get("inputs", {}) if isinstance(config.get("inputs"), dict) else {}


def resolve_secret(config: dict, name: str) -> Optional[str]:
    secret_block = config.get("secrets")
    if isinstance(secret_block, dict):
        value = secret_block.get(name)
        if value:
            return str(value)
    value = os.getenv(name)
    if value:
        return value
    return None


def _error_result(message: str, *, error_code: str, dry_run: bool, command: str, details: Optional[dict] = None) -> dict:
    payload = {
        "status": "error",
        "error_code": error_code,
        "message": message,
        "dry_run": dry_run,
        "command": command,
    }
    if details:
        payload["details"] = details
    return payload


class ProphetApi:
    def __init__(self, session_token: str, base_url: Optional[str] = None):
        if not session_token:
            raise ValueError("PROPHET_SESSION_TOKEN is required")
        self.session_token = session_token
        self.base_url = (base_url or os.getenv("PROPHET_BASE_URL") or DEFAULT_PROPHET_BASE_URL).rstrip("/")

    def _request(self, query: str, operation_name: str) -> Dict[str, Any]:
        url = f"{self.base_url}/api/graphql"
        body = {"query": query, "operationName": operation_name}
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
                payload = resp.read().decode("utf-8")
        except Exception as exc:
            raise ProphetAuthError(f"Prophet auth probe failed: {exc}") from exc

        try:
            return json.loads(payload) if payload else {}
        except json.JSONDecodeError as exc:
            raise ProphetAuthError("Prophet auth probe returned invalid JSON") from exc

    def _request_with_variables(self, query: str, operation_name: str, variables: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base_url}/api/graphql"
        body = {"query": query, "operationName": operation_name, "variables": variables}
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
                payload = resp.read().decode("utf-8")
        except Exception as exc:
            raise ProphetSkillError(f"Prophet API request failed: {exc}") from exc

        try:
            return json.loads(payload) if payload else {}
        except json.JSONDecodeError as exc:
            raise ProphetSkillError("Prophet API returned invalid JSON") from exc

    def viewer_wallet_balance(self) -> dict:
        payload = self._request(VIEWER_WALLET_BALANCE_QUERY, "ViewerWalletBalance")
        viewer = payload.get("data", {}).get("viewer")
        if not viewer:
            raise ProphetAuthError(
                "Prophet session token was accepted by the endpoint but did not resolve an authenticated viewer"
            )
        return viewer

    def initiate_market(self, question: str) -> Dict[str, Any]:
        payload = self._request_with_variables(
            INITIATE_MARKET_MUTATION,
            "InitiateMarket",
            {"input": {"question": question}},
        )
        errors = payload.get("errors")
        if errors:
            raise ProphetSkillError(f"initiateMarket failed: {errors[0].get('message', errors)}")
        result = payload.get("data", {}).get("initiateMarket")
        if not result:
            raise ProphetSkillError("initiateMarket returned no data")
        return result


class SerenApi:
    def __init__(self, api_key: str, api_base: Optional[str] = None):
        if not api_key:
            raise ValueError("SEREN_API_KEY is required")
        self.api_key = api_key
        self.api_base = (api_base or os.getenv("SEREN_API_BASE") or "https://api.serendb.com/publishers/seren-db").rstrip("/")

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
            raise SerenBootstrapError(f"Seren API request failed ({method} {path}): {exc}") from exc

        try:
            return json.loads(payload) if payload else {}
        except json.JSONDecodeError as exc:
            raise SerenBootstrapError(f"Seren API returned invalid JSON for {method} {path}") from exc

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
        raise SerenBootstrapError("Could not resolve connection string from Seren API")


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
        raise SerenBootstrapError("Unable to determine Prophet storage project_id")

    branches = api.list_branches(project_id)
    if not branches:
        raise SerenBootstrapError(f"No branches available for Prophet storage project {project_id}")

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
        raise SerenBootstrapError("Unable to determine Prophet storage branch_id")

    databases = api.list_databases(project_id, branch_id)
    db_names = {str(d.get("name")) for d in databases if d.get("name")}
    created_database = False
    if database_name not in db_names:
        api.create_database(project_id=project_id, branch_id=branch_id, name=database_name)
        created_database = True

    conn = _patch_database(api.get_connection_string(project_id=project_id, branch_id=branch_id), database_name)
    target = SerenDbTarget(
        project_id=project_id,
        branch_id=branch_id,
        database_name=database_name,
        connection_string=conn,
        project_name=str(project.get("name") or project_name),
        branch_name=branch_name,
        created_project=created_project,
        created_database=created_database,
    )
    return target


def storage_bootstrap_sql(schema_name: str) -> List[str]:
    if not SCHEMA_PATH.exists():
        raise SerenBootstrapError(f"Schema file not found: {SCHEMA_PATH}")
    raw = SCHEMA_PATH.read_text(encoding="utf-8")
    rendered = raw.replace("{{schema_name}}", schema_name)
    statements = [part.strip() for part in rendered.split(";") if part.strip()]
    if not statements:
        raise SerenBootstrapError(f"Schema file is empty: {SCHEMA_PATH}")
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
        raise SerenBootstrapError(f"Failed to apply Prophet storage bootstrap: {exc}") from exc
    return len(statements)


def ensure_storage(config: dict) -> dict:
    storage_cfg = config.get("storage") if isinstance(config.get("storage"), dict) else {}
    if not _bool(storage_cfg.get("auto_bootstrap"), True):
        return {
            "status": "skipped",
            "reason": "auto_bootstrap_disabled",
            "schema_name": str(storage_cfg.get("schema_name") or DEFAULT_SCHEMA_NAME),
        }

    project_name = str(storage_cfg.get("project_name") or DEFAULT_PROJECT_NAME)
    database_name = str(storage_cfg.get("database_name") or DEFAULT_DATABASE_NAME)
    schema_name = str(storage_cfg.get("schema_name") or DEFAULT_SCHEMA_NAME)
    region = str(storage_cfg.get("region") or DEFAULT_REGION)
    connection_string = storage_cfg.get("connection_string") or os.getenv("SERENDB_URL")
    api_key = resolve_secret(config, "SEREN_API_KEY")

    target: Optional[SerenDbTarget] = None
    if not connection_string:
        if not api_key:
            raise SerenBootstrapError(
                f"SEREN_API_KEY is required to auto-provision Prophet storage. Create an account at {SEREN_SKILLS_DOCS_URL}."
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
        "auto_provisioned": True,
        "connection_string": connection_string,
    }
    if target:
        result.update(
            {
                "project_id": target.project_id,
                "branch_id": target.branch_id,
                "branch_name": target.branch_name,
                "created_project": bool(getattr(target, "created_project", False)),
                "created_database": bool(getattr(target, "created_database", False)),
            }
        )
    return result


@dataclass
class MarketCandidate:
    candidate_id: str
    category: str
    question: str
    score: float = 0.0
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SubmissionResult:
    submission_id: str
    candidate_id: str
    status: str
    payload: Dict[str, Any] = field(default_factory=dict)
    prophet_market_id: Optional[str] = None


@dataclass
class PipelineContext:
    session_id: str
    run_id: str
    command: str
    dry_run: bool
    referral_code: str
    candidate_limit: int
    submit_limit: int
    strict_mode: bool
    token: str
    connection_string: Optional[str] = None
    candidates: List[MarketCandidate] = field(default_factory=list)
    filtered: List[MarketCandidate] = field(default_factory=list)
    submissions: List[SubmissionResult] = field(default_factory=list)
    events: List[Dict[str, Any]] = field(default_factory=list)


def _make_id() -> str:
    return uuid.uuid4().hex[:16]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def generate_market_candidates(ctx: PipelineContext) -> List[MarketCandidate]:
    """Generate candidate market questions from templates."""
    now = datetime.now(timezone.utc)
    year = now.year
    quarter = (now.month - 1) // 3 + 1
    next_quarter = quarter + 1 if quarter < 4 else 1
    next_q_year = year if next_quarter > quarter else year + 1
    end_of_year = f"December 31, {year}"
    end_of_quarter = f"{'March 31' if next_quarter == 1 else 'June 30' if next_quarter == 2 else 'September 30' if next_quarter == 3 else 'December 31'}, {next_q_year}"

    fill_vars = {
        "date": end_of_quarter,
        "year": str(year),
        "quarter": str(next_quarter),
        "pct": "3",
        "level": "6000",
        "amount": "500",
        "btc_price": "120",
        "eth_price": "5",
        "mcap": "4",
        "company": "Apple",
        "team": "the Lakers",
        "league": "NBA",
        "office": "Presidential",
        "topic": "AI regulation",
        "price": "250",
        "movie_or_show": "a streaming original",
    }

    candidates: List[MarketCandidate] = []
    for category, template in CANDIDATE_TEMPLATES:
        if len(candidates) >= ctx.candidate_limit:
            break
        try:
            question = template.format(**fill_vars)
        except KeyError:
            continue
        candidates.append(MarketCandidate(
            candidate_id=_make_id(),
            category=category,
            question=question,
        ))

    ctx.events.append({"event_type": "candidates_generated", "payload": {"count": len(candidates)}})
    return candidates


def score_market_candidates(candidates: List[MarketCandidate]) -> List[MarketCandidate]:
    """Score candidates on clarity and category diversity."""
    seen_categories: Dict[str, int] = {}
    for c in candidates:
        seen_categories[c.category] = seen_categories.get(c.category, 0) + 1
        clarity = 1.0 if c.question.endswith("?") else 0.5
        has_date = 1.0 if any(w in c.question for w in ["by", "in 20", "Q1", "Q2", "Q3", "Q4"]) else 0.3
        diversity = 1.0 / seen_categories[c.category]
        c.score = round(clarity * 0.3 + has_date * 0.3 + diversity * 0.4, 4)
    return sorted(candidates, key=lambda c: c.score, reverse=True)


def filter_market_candidates(
    candidates: List[MarketCandidate],
    submit_limit: int,
    recent_titles: List[str],
) -> List[MarketCandidate]:
    """Dedup against recent submissions and apply submit_limit."""
    recent_lower = {t.lower() for t in recent_titles}
    filtered = [c for c in candidates if c.question.lower() not in recent_lower]
    return filtered[:submit_limit]


def load_recent_submissions(connection_string: Optional[str], schema_name: str) -> List[str]:
    """Load titles of recent submissions from SerenDB for dedup."""
    if not connection_string:
        return []
    try:
        with psycopg_connect(connection_string) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT payload->>'question' FROM {schema_name}.market_submissions "
                    f"WHERE created_at > NOW() - INTERVAL '7 days' AND payload->>'question' IS NOT NULL"
                )
                return [row[0] for row in cur.fetchall()]
    except Exception:
        return []


def _create_market_via_playwright(
    question: str,
    base_url: str,
) -> Optional[str]:
    """Attempt to create a market on Prophet via browser UI.

    Returns the prophet market ID on success, or None on failure.
    Requires playwright and a browser with an active Prophet session.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp("http://localhost:9222")
            context = browser.contexts[0] if browser.contexts else None
            if not context:
                return None
            page = context.pages[0] if context.pages else context.new_page()

            page.goto(f"{base_url}/create", wait_until="networkidle", timeout=15000)

            # Fill the question textarea
            textarea = page.locator("textarea").first
            textarea.fill(question)
            textarea.press("Enter")

            # Wait for validation result to appear
            page.wait_for_timeout(3000)

            # Click the create/submit button
            create_btn = page.locator("button:has-text('Create'), button:has-text('Submit'), button:has-text('Confirm')").first
            if create_btn.is_visible():
                create_btn.click()
                page.wait_for_timeout(5000)

            # Check if we landed on a market page
            current_url = page.url
            if "/market/" in current_url or "/markets/" in current_url:
                # Extract market ID from URL
                parts = current_url.rstrip("/").split("/")
                return parts[-1] if parts else None

            # Try to find market ID in the page
            market_link = page.locator("a[href*='/market/']").first
            if market_link.is_visible():
                href = market_link.get_attribute("href") or ""
                parts = href.rstrip("/").split("/")
                return parts[-1] if parts else None

            return None
    except Exception:
        return None


def submit_market_batch(
    ctx: PipelineContext,
    candidates: List[MarketCandidate],
    api: ProphetApi,
) -> List[SubmissionResult]:
    """Validate candidates via initiateMarket, then create via Playwright UI."""
    results: List[SubmissionResult] = []
    for c in candidates:
        sub_id = _make_id()
        if ctx.dry_run:
            results.append(SubmissionResult(
                submission_id=sub_id,
                candidate_id=c.candidate_id,
                status="dry_run_skipped",
                payload={"question": c.question, "category": c.category},
            ))
            ctx.events.append({"event_type": "submission_skipped", "payload": {"candidate_id": c.candidate_id, "reason": "dry_run"}})
            continue

        try:
            validation = api.initiate_market(c.question)
            is_valid = validation.get("isValid", False)
            base_payload = {
                "question": c.question,
                "category": c.category,
                "is_valid": is_valid,
                "suggestion": validation.get("suggestion"),
                "title": validation.get("title"),
                "resolution_date": validation.get("resolutionDate"),
                "resolution_rules": validation.get("resolutionRules"),
            }

            if not is_valid:
                results.append(SubmissionResult(
                    submission_id=sub_id,
                    candidate_id=c.candidate_id,
                    status="rejected",
                    payload=base_payload,
                ))
                ctx.events.append({"event_type": "submission_completed", "payload": {"candidate_id": c.candidate_id, "status": "rejected"}})
                continue

            # Validated — attempt creation via Playwright
            prophet_market_id = None
            status = "validated"
            try:
                prophet_market_id = _create_market_via_playwright(c.question, api.base_url)
                if prophet_market_id:
                    status = "created"
                    ctx.events.append({"event_type": "market_created", "payload": {"candidate_id": c.candidate_id, "prophet_market_id": prophet_market_id}})
                else:
                    base_payload["creation_error"] = "Playwright creation returned no market ID"
                    ctx.events.append({"event_type": "creation_failed", "payload": {"candidate_id": c.candidate_id, "reason": "no_market_id"}})
            except Exception as create_exc:
                base_payload["creation_error"] = str(create_exc)
                ctx.events.append({"event_type": "creation_failed", "payload": {"candidate_id": c.candidate_id, "error": str(create_exc)}})

            results.append(SubmissionResult(
                submission_id=sub_id,
                candidate_id=c.candidate_id,
                status=status,
                payload=base_payload,
                prophet_market_id=prophet_market_id,
            ))
            ctx.events.append({"event_type": "submission_completed", "payload": {"candidate_id": c.candidate_id, "status": status}})

        except ProphetSkillError as exc:
            results.append(SubmissionResult(
                submission_id=sub_id,
                candidate_id=c.candidate_id,
                status="error",
                payload={"question": c.question, "error": str(exc)},
            ))
            ctx.events.append({"event_type": "submission_error", "payload": {"candidate_id": c.candidate_id, "error": str(exc)}})
    return results


def persist_run(ctx: PipelineContext, schema_name: str) -> Dict[str, int]:
    """Write session, run, candidates, submissions, events to SerenDB."""
    if not ctx.connection_string:
        return {"persisted": 0, "reason": "no_connection_string"}

    counts: Dict[str, int] = {}
    try:
        with psycopg_connect(ctx.connection_string) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"INSERT INTO {schema_name}.sessions (session_id, referral_code, command) "
                    f"VALUES (%s, %s, %s) ON CONFLICT (session_id) DO NOTHING",
                    (ctx.session_id, ctx.referral_code, ctx.command),
                )
                counts["sessions"] = 1

                cur.execute(
                    f"INSERT INTO {schema_name}.runs (run_id, session_id, status, dry_run) "
                    f"VALUES (%s, %s, %s, %s) ON CONFLICT (run_id) DO NOTHING",
                    (ctx.run_id, ctx.session_id, "completed", ctx.dry_run),
                )
                counts["runs"] = 1

                for c in ctx.candidates:
                    cur.execute(
                        f"INSERT INTO {schema_name}.market_candidates "
                        f"(candidate_id, run_id, title, score, payload) "
                        f"VALUES (%s, %s, %s, %s, %s) ON CONFLICT (candidate_id) DO NOTHING",
                        (c.candidate_id, ctx.run_id, c.question, c.score, json.dumps({"category": c.category})),
                    )
                counts["market_candidates"] = len(ctx.candidates)

                for s in ctx.submissions:
                    cur.execute(
                        f"INSERT INTO {schema_name}.market_submissions "
                        f"(submission_id, run_id, candidate_id, status, prophet_market_id, payload) "
                        f"VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (submission_id) DO NOTHING",
                        (s.submission_id, ctx.run_id, s.candidate_id, s.status, s.prophet_market_id, json.dumps(s.payload)),
                    )
                counts["market_submissions"] = len(ctx.submissions)

                for evt in ctx.events:
                    cur.execute(
                        f"INSERT INTO {schema_name}.events (run_id, event_type, payload) "
                        f"VALUES (%s, %s, %s)",
                        (ctx.run_id, evt["event_type"], json.dumps(evt.get("payload", {}))),
                    )
                counts["events"] = len(ctx.events)

            conn.commit()
    except Exception as exc:
        return {"persisted": 0, "error": str(exc)}
    return counts


def render_report(ctx: PipelineContext, storage_result: dict, persist_counts: dict) -> dict:
    """Build the final structured run report."""
    validated = [s for s in ctx.submissions if s.status == "validated"]
    created = [s for s in ctx.submissions if s.status == "created"]
    rejected = [s for s in ctx.submissions if s.status == "rejected"]
    skipped = [s for s in ctx.submissions if s.status == "dry_run_skipped"]
    errored = [s for s in ctx.submissions if s.status == "error"]

    return {
        "status": "ok",
        "skill": "prophet-market-seeder",
        "command": ctx.command,
        "dry_run": ctx.dry_run,
        "session_id": ctx.session_id,
        "run_id": ctx.run_id,
        "referral_code": ctx.referral_code,
        "pipeline": {
            "candidates_generated": len(ctx.candidates),
            "candidates_filtered": len(ctx.filtered),
            "submissions_validated": len(validated),
            "submissions_created": len(created),
            "submissions_rejected": len(rejected),
            "submissions_skipped": len(skipped),
            "submissions_errored": len(errored),
        },
        "submissions": [
            {
                "candidate_id": s.candidate_id,
                "status": s.status,
                "question": s.payload.get("question", ""),
                "title": s.payload.get("title"),
                "is_valid": s.payload.get("is_valid"),
                "prophet_market_id": s.prophet_market_id,
            }
            for s in ctx.submissions
        ],
        "storage": storage_result,
        "persistence": persist_counts,
    }


def validate_prophet_access(config: dict) -> dict:
    token = resolve_secret(config, "PROPHET_SESSION_TOKEN")
    if not token:
        raise ProphetAuthError(
            "Missing PROPHET_SESSION_TOKEN. Use the Privy JWT from localStorage['privy:token'] and send it as Authorization: Bearer <token>."
        )
    viewer = ProphetApi(token).viewer_wallet_balance()
    wallet_balance = viewer.get("walletBalance") or {}
    return {
        "status": "ok",
        "required_header": "Authorization: Bearer <PROPHET_SESSION_TOKEN>",
        "token_source": "localStorage['privy:token'] from an authenticated Prophet browser session",
        "viewer": {
            "wallet_balance_total_cents": wallet_balance.get("totalCents"),
            "wallet_balance_available_cents": wallet_balance.get("availableCents"),
            "safe_address": wallet_balance.get("safeAddress"),
            "safe_deployed": wallet_balance.get("safeDeployed"),
        },
    }


def resolve_testnet_config(config: dict) -> Optional[dict]:
    testnet_cfg = config.get("testnet") if isinstance(config.get("testnet"), dict) else {}
    enabled = _bool(testnet_cfg.get("enabled") or os.getenv("PROPHET_TESTNET_MODE"), False)
    if not enabled:
        return None
    return {
        "enabled": True,
        "base_url": str(testnet_cfg.get("base_url") or os.getenv("PROPHET_TESTNET_BASE_URL") or DEFAULT_PROPHET_TESTNET_BASE_URL),
        "usdc_faucet": str(testnet_cfg.get("usdc_faucet") or PROPHET_TESTNET_USDC_FAUCET),
    }


def run_once(config: dict, dry_run: bool) -> dict:
    inputs = _config_inputs(config)
    command = _normalize_command(inputs.get("command"))
    strict_mode = _bool(inputs.get("strict_mode"), True)
    referral_code = str(inputs.get("referral_code") or "AGENTACCESS")
    candidate_limit = int(inputs.get("candidate_limit") or 12)
    submit_limit = int(inputs.get("submit_limit") or 3)

    if candidate_limit < 1 or submit_limit < 1 or submit_limit > candidate_limit:
        return _error_result(
            "Invalid candidate or submit limits",
            error_code="invalid_limits",
            dry_run=dry_run,
            command=command,
            details={"candidate_limit": candidate_limit, "submit_limit": submit_limit},
        )

    try:
        storage = ensure_storage(config) if command in {"setup", "run"} else {"status": "not_run"}
        auth = validate_prophet_access(config)
    except ProphetSkillError as exc:
        if strict_mode or command != "setup":
            error_code = "missing_seren_api_key" if SEREN_SKILLS_DOCS_URL in str(exc) else "auth_or_bootstrap_failed"
            details = {"docs_url": SEREN_SKILLS_DOCS_URL} if error_code == "missing_seren_api_key" else None
            return _error_result(str(exc), error_code=error_code, dry_run=dry_run, command=command, details=details)
        storage = {"status": "skipped", "reason": "setup_non_strict"}
        auth = {
            "status": "warning",
            "message": str(exc),
            "required_header": "Authorization: Bearer <PROPHET_SESSION_TOKEN>",
            "token_source": "localStorage['privy:token'] from an authenticated Prophet browser session",
        }

    testnet = resolve_testnet_config(config)

    if command != "run":
        run_summary = {
            "status": "ok",
            "skill": "prophet-market-seeder",
            "command": command,
            "dry_run": dry_run,
            "connectors": AVAILABLE_CONNECTORS,
            "input_keys": sorted(inputs.keys()),
            "auth": auth,
            "storage": storage,
            "limits": {"candidate_limit": candidate_limit, "submit_limit": submit_limit},
            "referral_code": referral_code,
        }
        if testnet:
            run_summary["testnet"] = testnet
        return run_summary

    # --- Full pipeline for command=run ---
    token = resolve_secret(config, "PROPHET_SESSION_TOKEN") or ""
    storage_cfg = config.get("storage") if isinstance(config.get("storage"), dict) else {}
    schema_name = str(storage_cfg.get("schema_name") or DEFAULT_SCHEMA_NAME)

    ctx = PipelineContext(
        session_id=_make_id(),
        run_id=_make_id(),
        command=command,
        dry_run=dry_run,
        referral_code=referral_code,
        candidate_limit=candidate_limit,
        submit_limit=submit_limit,
        strict_mode=strict_mode,
        token=token,
        connection_string=storage.get("connection_string"),
    )

    # Step 6: generate candidates
    ctx.candidates = generate_market_candidates(ctx)

    # Step 7: score candidates
    ctx.candidates = score_market_candidates(ctx.candidates)

    # Step 8: filter candidates (dedup + limit)
    recent_titles = load_recent_submissions(ctx.connection_string, schema_name)
    ctx.filtered = filter_market_candidates(ctx.candidates, submit_limit, recent_titles)

    # Step 9: submit candidates
    api = ProphetApi(token) if token else None
    if api:
        ctx.submissions = submit_market_batch(ctx, ctx.filtered, api)
    else:
        ctx.events.append({"event_type": "submission_skipped", "payload": {"reason": "no_token"}})

    # Step 10: persist run
    persist_counts = persist_run(ctx, schema_name)

    # Step 11: render report
    report = render_report(ctx, storage, persist_counts)
    if testnet:
        report["testnet"] = testnet
    return report


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    dry_run = _bool(config.get("dry_run"), DEFAULT_DRY_RUN)
    result = run_once(config=config, dry_run=dry_run)
    print(json.dumps(result))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
