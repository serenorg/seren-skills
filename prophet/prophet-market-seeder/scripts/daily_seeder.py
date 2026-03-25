#!/usr/bin/env python3
"""Automated daily seeder: Gmail OTP auth + Polymarket cross-seed → Prophet."""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Re-use the existing agent module
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import agent as seeder_agent

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
PRIVY_OTP_SUBJECT_PATTERN = re.compile(r"privy|verification|login code", re.IGNORECASE)
OTP_CODE_PATTERN = re.compile(r"\b(\d{6})\b")
DEFAULT_PROPHET_EMAIL = "taariq@serendb.com"
DEFAULT_SUBMIT_LIMIT = 10
POLYMARKET_INTEL_PROJECT_ID = "523e15b9-5129-4d36-8072-1c6d8fa031fc"
POLYMARKET_INTEL_BRANCH_ID = "dffd1d2e-5765-4e00-804e-55c8bade32e3"
POLYMARKET_INTEL_DB = "serendb"
GOOGLE_TOKENS_PROJECT_ID = "a4f05a57-24bb-46bf-b95d-0d98632f9295"
GOOGLE_TOKENS_BRANCH_ID = "23c8277e-7cc2-4704-9542-a9c13f954850"
GOOGLE_TOKENS_DB = "serendb"

POLYMARKET_CANDIDATES_QUERY = """
SELECT title, probability, volume_usd, liquidity_usd, resolution_date
FROM public.platform_markets
WHERE is_resolved = false
  AND resolution_date > CURRENT_DATE
  AND resolution_date < CURRENT_DATE + INTERVAL '365 days'
  AND probability BETWEEN 0.15 AND 0.85
  AND volume_usd > 50000
ORDER BY volume_usd DESC
LIMIT %s
"""


class DailySeederError(RuntimeError):
    """Raised when any step in the daily pipeline fails."""


# ---------------------------------------------------------------------------
# Gmail OTP retrieval
# ---------------------------------------------------------------------------

@dataclass
class GmailCredentials:
    client_id: str
    client_secret: str
    refresh_token: str
    email: str


def load_gmail_credentials(
    connection_string: str,
    email: str = DEFAULT_PROPHET_EMAIL,
) -> GmailCredentials:
    """Load Gmail OAuth refresh token from google-auth-tokens DB."""
    client_id = os.getenv("GOOGLE_CLIENT_ID", "")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise DailySeederError(
            "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set for Gmail OTP retrieval"
        )

    try:
        with seeder_agent.psycopg_connect(connection_string) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT refresh_token_encrypted FROM public.google_tokens WHERE email = %s",
                    (email,),
                )
                row = cur.fetchone()
                if not row:
                    raise DailySeederError(f"No Google token found for {email}")
                return GmailCredentials(
                    client_id=client_id,
                    client_secret=client_secret,
                    refresh_token=row[0],
                    email=email,
                )
    except DailySeederError:
        raise
    except Exception as exc:
        raise DailySeederError(f"Failed to load Gmail credentials: {exc}") from exc


def gmail_get_access_token(creds: GmailCredentials) -> str:
    """Exchange refresh token for a short-lived access token."""
    body = urllib.parse.urlencode({
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "refresh_token": creds.refresh_token,
        "grant_type": "refresh_token",
    }).encode("utf-8")
    req = urllib.request.Request(GOOGLE_TOKEN_URL, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return payload["access_token"]
    except Exception as exc:
        raise DailySeederError(f"Gmail token exchange failed: {exc}") from exc


def gmail_find_otp(access_token: str, max_age_seconds: int = 120) -> str:
    """Search Gmail for the most recent Privy OTP email and extract the 6-digit code."""
    after_epoch = int(time.time()) - max_age_seconds
    query = urllib.parse.urlencode({
        "q": f"from:privy.io after:{after_epoch}",
        "maxResults": "3",
    })
    url = f"{GMAIL_API_BASE}/messages?{query}"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {access_token}")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise DailySeederError(f"Gmail search failed: {exc}") from exc

    messages = data.get("messages", [])
    if not messages:
        raise DailySeederError("No recent Privy OTP email found")

    msg_id = messages[0]["id"]
    msg_url = f"{GMAIL_API_BASE}/messages/{msg_id}?format=full"
    req2 = urllib.request.Request(msg_url)
    req2.add_header("Authorization", f"Bearer {access_token}")

    try:
        with urllib.request.urlopen(req2, timeout=15) as resp:
            msg_data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise DailySeederError(f"Gmail message fetch failed: {exc}") from exc

    body_text = _extract_email_body(msg_data)
    match = OTP_CODE_PATTERN.search(body_text)
    if not match:
        raise DailySeederError(f"Could not find 6-digit OTP in email body")
    return match.group(1)


def _extract_email_body(msg_data: dict) -> str:
    """Extract plain text body from Gmail message payload."""
    payload = msg_data.get("payload", {})

    # Check snippet first (often contains the OTP)
    snippet = msg_data.get("snippet", "")
    if snippet:
        match = OTP_CODE_PATTERN.search(snippet)
        if match:
            return snippet

    # Check direct body
    body_data = payload.get("body", {}).get("data", "")
    if body_data:
        return base64.urlsafe_b64decode(body_data).decode("utf-8", errors="replace")

    # Check parts
    for part in payload.get("parts", []):
        mime = part.get("mimeType", "")
        if mime == "text/plain":
            data = part.get("body", {}).get("data", "")
            if data:
                return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

    return snippet


# ---------------------------------------------------------------------------
# Playwright auto-auth
# ---------------------------------------------------------------------------

def auto_authenticate_playwright(
    email: str,
    gmail_creds: GmailCredentials,
) -> str:
    """Full Playwright auto-auth flow: navigate → OTP → Gmail retrieve → complete → return JWT."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise DailySeederError(
            "playwright is required for auto-auth. Install with: pip install playwright && playwright install chromium"
        )

    access_token = gmail_get_access_token(gmail_creds)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        # Navigate to Prophet
        page.goto("https://app.prophetmarket.ai", wait_until="networkidle")

        # Check if already authenticated
        existing = page.evaluate("localStorage.getItem('privy:token')")
        if existing:
            token = existing.strip('"')
            browser.close()
            return token

        # Click sign in
        page.click("button:has-text('Sign in')", timeout=10000)
        page.wait_for_selector("#email-input", timeout=10000)

        # Fill email and submit
        page.fill("#email-input", email)
        page.click("button:has-text('Submit')")
        page.wait_for_selector("input[name='code-0']", timeout=15000)

        # Wait for OTP email to arrive
        time.sleep(5)

        # Retrieve OTP from Gmail
        otp = gmail_find_otp(access_token, max_age_seconds=120)

        # Fill OTP digits
        for i, digit in enumerate(otp):
            page.fill(f"input[name='code-{i}']", digit)

        # Poll for token
        token = None
        for _ in range(20):
            time.sleep(3)
            raw = page.evaluate("localStorage.getItem('privy:token')")
            if raw:
                token = raw.strip('"')
                break

        browser.close()

        if not token:
            raise DailySeederError("Privy token not found after OTP entry")
        return token


# ---------------------------------------------------------------------------
# Polymarket candidate source
# ---------------------------------------------------------------------------

def fetch_polymarket_candidates(
    seren_api_key: str,
    limit: int = 30,
) -> List[Dict[str, Any]]:
    """Query seren-polymarket-intelligence for contested markets."""
    api = seeder_agent.SerenApi(api_key=seren_api_key)

    # Get connection string for the intelligence DB
    conn_str = api.get_connection_string(
        project_id=POLYMARKET_INTEL_PROJECT_ID,
        branch_id=POLYMARKET_INTEL_BRANCH_ID,
    )
    conn_str = seeder_agent._patch_database(conn_str, POLYMARKET_INTEL_DB)

    try:
        with seeder_agent.psycopg_connect(conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute(POLYMARKET_CANDIDATES_QUERY, (limit,))
                columns = [desc[0] for desc in cur.description]
                rows = [dict(zip(columns, row)) for row in cur.fetchall()]
                return rows
    except Exception as exc:
        raise DailySeederError(f"Polymarket intelligence query failed: {exc}") from exc


def score_polymarket_candidate(row: dict) -> float:
    """Score a Polymarket market for Prophet cross-seeding."""
    prob = float(row.get("probability", 0.5))
    volume = float(row.get("volume_usd", 0))
    # Contestedness: max at 0.5, min at 0/1
    contestedness = 1.0 - abs(prob - 0.5) * 2
    # Volume score: log-scaled, capped
    import math
    vol_score = min(math.log10(max(volume, 1)) / 8.0, 1.0)
    return round(contestedness * 0.6 + vol_score * 0.4, 4)


def polymarket_to_prophet_questions(
    rows: List[Dict[str, Any]],
    submit_limit: int,
    recent_titles: List[str],
) -> List[str]:
    """Score, dedup, and convert Polymarket markets to Prophet questions."""
    recent_lower = {t.lower() for t in recent_titles}

    scored = []
    for row in rows:
        title = row.get("title", "")
        if not title or title.lower() in recent_lower:
            continue
        score = score_polymarket_candidate(row)
        scored.append((score, title))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [title for _, title in scored[:submit_limit]]


# ---------------------------------------------------------------------------
# Submit to Prophet
# ---------------------------------------------------------------------------

def submit_questions_to_prophet(
    token: str,
    questions: List[str],
) -> List[Dict[str, Any]]:
    """Submit a list of questions to Prophet via initiateMarket."""
    api = seeder_agent.ProphetApi(session_token=token)
    results = []
    for q in questions:
        try:
            validation = api.initiate_market(q)
            results.append({
                "question": q,
                "status": "accepted" if validation.get("isValid") else "rejected",
                "title": validation.get("title"),
                "is_valid": validation.get("isValid"),
                "resolution_date": validation.get("resolutionDate"),
            })
        except seeder_agent.ProphetSkillError as exc:
            results.append({
                "question": q,
                "status": "error",
                "error": str(exc),
            })
    return results


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def persist_daily_run(
    connection_string: str,
    schema_name: str,
    session_id: str,
    run_id: str,
    referral_code: str,
    questions: List[str],
    results: List[Dict[str, Any]],
) -> Dict[str, int]:
    """Persist the daily seeder run to SerenDB."""
    counts: Dict[str, int] = {}
    try:
        with seeder_agent.psycopg_connect(connection_string) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"INSERT INTO {schema_name}.sessions (session_id, referral_code, command) "
                    f"VALUES (%s, %s, %s) ON CONFLICT (session_id) DO NOTHING",
                    (session_id, referral_code, "daily_seed"),
                )
                counts["sessions"] = 1

                cur.execute(
                    f"INSERT INTO {schema_name}.runs (run_id, session_id, status, dry_run) "
                    f"VALUES (%s, %s, %s, %s) ON CONFLICT (run_id) DO NOTHING",
                    (run_id, session_id, "completed", False),
                )
                counts["runs"] = 1

                for r in results:
                    sub_id = seeder_agent._make_id()
                    cur.execute(
                        f"INSERT INTO {schema_name}.market_submissions "
                        f"(submission_id, run_id, candidate_id, status, payload) "
                        f"VALUES (%s, %s, %s, %s, %s) ON CONFLICT (submission_id) DO NOTHING",
                        (sub_id, run_id, sub_id, r["status"], json.dumps(r)),
                    )
                counts["market_submissions"] = len(results)

                cur.execute(
                    f"INSERT INTO {schema_name}.events (run_id, event_type, payload) "
                    f"VALUES (%s, %s, %s)",
                    (run_id, "daily_seed_completed", json.dumps({
                        "total": len(results),
                        "accepted": sum(1 for r in results if r["status"] == "accepted"),
                        "rejected": sum(1 for r in results if r["status"] == "rejected"),
                        "errored": sum(1 for r in results if r["status"] == "error"),
                    })),
                )
                counts["events"] = 1

            conn.commit()
    except Exception as exc:
        return {"persisted": 0, "error": str(exc)}
    return counts


# ---------------------------------------------------------------------------
# Google tokens connection string
# ---------------------------------------------------------------------------

def get_google_tokens_connection(seren_api_key: str) -> str:
    """Get connection string for the google-auth-tokens DB."""
    api = seeder_agent.SerenApi(api_key=seren_api_key)
    conn_str = api.get_connection_string(
        project_id=GOOGLE_TOKENS_PROJECT_ID,
        branch_id=GOOGLE_TOKENS_BRANCH_ID,
    )
    return seeder_agent._patch_database(conn_str, GOOGLE_TOKENS_DB)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_daily_seed(config_path: str = "config.json") -> dict:
    """Execute the full daily seeder pipeline."""
    config = seeder_agent.load_config(config_path)
    inputs = config.get("inputs", {})
    storage_cfg = config.get("storage", {})

    seren_api_key = seeder_agent.resolve_secret(config, "SEREN_API_KEY")
    if not seren_api_key:
        return {"status": "error", "error": "SEREN_API_KEY required"}

    prophet_email = inputs.get("prophet_email") or DEFAULT_PROPHET_EMAIL
    referral_code = inputs.get("referral_code") or "AGENTACCESS"
    submit_limit = int(inputs.get("submit_limit") or DEFAULT_SUBMIT_LIMIT)
    schema_name = storage_cfg.get("schema_name") or seeder_agent.DEFAULT_SCHEMA_NAME

    session_id = seeder_agent._make_id()
    run_id = seeder_agent._make_id()

    # Step 1: Bootstrap storage
    try:
        storage = seeder_agent.ensure_storage(config)
    except seeder_agent.ProphetSkillError as exc:
        return {"status": "error", "step": "storage", "error": str(exc)}

    connection_string = storage.get("connection_string")

    # Step 2: Auto-authenticate
    token = seeder_agent.resolve_secret(config, "PROPHET_SESSION_TOKEN")
    if not token:
        try:
            google_conn = get_google_tokens_connection(seren_api_key)
            gmail_creds = load_gmail_credentials(google_conn, prophet_email)
            token = auto_authenticate_playwright(prophet_email, gmail_creds)
        except DailySeederError as exc:
            return {"status": "error", "step": "auth", "error": str(exc)}

    # Step 3: Validate token
    try:
        seeder_agent.ProphetApi(token).viewer_wallet_balance()
    except seeder_agent.ProphetAuthError as exc:
        return {"status": "error", "step": "token_validation", "error": str(exc)}

    # Step 4: Fetch Polymarket candidates
    try:
        poly_rows = fetch_polymarket_candidates(seren_api_key, limit=submit_limit * 3)
    except DailySeederError as exc:
        return {"status": "error", "step": "polymarket_fetch", "error": str(exc)}

    # Step 5: Load recent submissions for dedup
    recent_titles = seeder_agent.load_recent_submissions(connection_string, schema_name)

    # Step 6: Score, dedup, select top N
    questions = polymarket_to_prophet_questions(poly_rows, submit_limit, recent_titles)
    if not questions:
        return {"status": "ok", "message": "No new candidates after dedup", "run_id": run_id}

    # Step 7: Submit to Prophet
    results = submit_questions_to_prophet(token, questions)

    # Step 8: Persist
    persist_counts = persist_daily_run(
        connection_string, schema_name, session_id, run_id, referral_code, questions, results,
    )

    accepted = [r for r in results if r["status"] == "accepted"]
    rejected = [r for r in results if r["status"] == "rejected"]
    errored = [r for r in results if r["status"] == "error"]

    return {
        "status": "ok",
        "skill": "prophet-market-seeder",
        "mode": "daily_seed",
        "session_id": session_id,
        "run_id": run_id,
        "referral_code": referral_code,
        "pipeline": {
            "polymarket_candidates_fetched": len(poly_rows),
            "questions_after_dedup": len(questions),
            "accepted": len(accepted),
            "rejected": len(rejected),
            "errored": len(errored),
        },
        "submissions": results,
        "persistence": persist_counts,
    }


def main() -> int:
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.json"
    result = run_daily_seed(config_path)
    print(json.dumps(result))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
