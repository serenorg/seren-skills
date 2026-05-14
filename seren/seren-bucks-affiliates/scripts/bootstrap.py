from __future__ import annotations

import json
import os
import ssl
from typing import Any
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from common import select_auth_path, utc_now

PUBLISHER = "seren-affiliates"
MAX_BOOTSTRAP_ATTEMPTS = 3
DRY_RUN_PARTNER_LINK = "https://serendb.com?ref=SRN_DRYRUN"


class GatewayError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        body: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body or ""


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi  # type: ignore[import-not-found]

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


class SerenAffiliateGateway:
    SEREN_API_BASE = "https://api.serendb.com"

    def __init__(self, *, api_key: str | None = None, timeout_seconds: float = 30.0) -> None:
        self.api_key = (
            api_key
            or os.environ.get("API_KEY")
            or os.environ.get("SEREN_API_KEY")
            or ""
        ).strip()
        self.timeout_seconds = timeout_seconds

    def call(
        self,
        publisher: str,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
    ) -> Any:
        if not self.api_key:
            raise GatewayError("SEREN_API_KEY or API_KEY is required for live affiliate bootstrap.")

        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        request = Request(
            f"{self.SEREN_API_BASE}/publishers/{publisher}{path}",
            headers=headers,
            method=method.upper(),
            data=data,
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds, context=_ssl_context()) as response:
                text = response.read().decode("utf-8")
        except HTTPError as exc:
            body_text = ""
            try:
                body_text = exc.read().decode("utf-8")
            except Exception:
                body_text = ""
            raise GatewayError(str(exc), status_code=exc.code, body=body_text) from exc
        except OSError as exc:
            raise GatewayError(str(exc)) from exc

        if not text:
            return {}
        return _unwrap_gateway_payload(json.loads(text))


def _unwrap_gateway_payload(payload: Any) -> Any:
    if isinstance(payload, dict):
        if "data" in payload and isinstance(payload["data"], dict):
            inner = payload["data"]
            if "body" in inner:
                return _unwrap_gateway_payload(inner["body"])
            return inner
        if "body" in payload:
            return _unwrap_gateway_payload(payload["body"])
    return payload


def _as_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("partner_links", "partnerLinks", "links", "programs", "items", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
        if isinstance(value, dict):
            nested = _as_list(value)
            if nested:
                return nested
    return []


def _first_text(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is not None:
            text = str(value).strip()
            if text:
                return text
    return ""


def _is_real_srn_link(link: str) -> bool:
    text = link.strip()
    if not text or "ref=default" in text.lower():
        return False
    parsed = urlparse(text)
    ref_values = parse_qs(parsed.query).get("ref", [])
    return any(value.startswith("SRN_") for value in ref_values) or "SRN_" in text


def _matches_seren_bucks(row: dict[str, Any], config: dict) -> bool:
    configured = {
        str(config["program"].get("program_id", "")).strip().lower(),
        str(config["program"].get("program_slug", "")).strip().lower(),
    }
    fields = [
        _first_text(row, "program_id", "programId", "id"),
        _first_text(row, "program_slug", "programSlug", "slug"),
        _first_text(row, "program_name", "programName", "name"),
    ]
    normalized = {field.lower() for field in fields if field}
    if any(field in configured for field in normalized):
        return True
    haystack = " ".join(normalized)
    return "seren" in haystack and "buck" in haystack


def _program_from_row(row: dict[str, Any], config: dict) -> dict[str, str]:
    return {
        "program_id": _first_text(
            row,
            "program_id",
            "programId",
            "program_slug",
            "programSlug",
            "slug",
            "id",
        )
        or str(config["program"]["program_id"]),
        "program_name": _first_text(row, "program_name", "programName", "name")
        or str(config["program"]["program_name"]),
        "tracked_link": _first_text(
            row,
            "partner_link_url",
            "partnerLinkUrl",
            "tracked_link",
            "trackedLink",
            "url",
            "link",
            "href",
        ),
        "source_of_truth": str(config["program"]["affiliate_source_of_truth"]),
    }


def _select_seren_bucks_program(payload: Any, config: dict) -> dict[str, str] | None:
    rows = _as_list(payload)
    if not rows:
        return None
    candidates = [row for row in rows if _matches_seren_bucks(row, config)]
    if not candidates and len(rows) == 1:
        candidates = rows
    for row in candidates:
        program = _program_from_row(row, config)
        if _is_real_srn_link(program["tracked_link"]):
            return program
    return _program_from_row(candidates[0], config) if candidates else None


def _apply_program(config: dict, program: dict[str, str]) -> dict[str, str]:
    config["program"]["program_id"] = program["program_id"]
    config["program"]["program_name"] = program["program_name"]
    config["program"]["tracked_link"] = program["tracked_link"]
    config.setdefault("inputs", {})["tracked_link"] = program["tracked_link"]
    return {
        "program_id": program["program_id"],
        "program_name": program["program_name"],
        "tracked_link": program["tracked_link"],
        "affiliate_source_of_truth": program["source_of_truth"],
        "updated_at": utc_now(),
    }


def _dry_run_partner_links(config: dict) -> dict[str, list[dict[str, str]]]:
    return {
        "partner_links": [
            {
                "program_id": str(config["program"]["program_id"]),
                "program_slug": str(config["program"].get("program_slug", "seren-bucks")),
                "program_name": str(config["program"]["program_name"]),
                "partner_link_url": DRY_RUN_PARTNER_LINK,
            }
        ]
    }


def _success(
    *,
    config: dict,
    program: dict[str, str],
    registered_this_run: bool,
    retry_count: int,
    affiliate_feed_status: str,
) -> dict:
    program_state = _apply_program(config, program)
    return {
        "status": "ok",
        "registered_this_run": registered_this_run,
        "retry_count": retry_count,
        "affiliate_feed_status": affiliate_feed_status,
        "program": program,
        "program_state": program_state,
        "persist": {"program_state": program_state},
    }


def _invalid_link_error(program: dict[str, str] | None, *, retry_count: int) -> dict:
    return {
        "status": "error",
        "error_code": "affiliate_tracked_link_invalid",
        "affiliate_feed_status": "invalid",
        "retry_count": retry_count,
        "fail_closed": True,
        "message": (
            "seren-affiliates did not return a SerenBucks partner link with a real SRN_ "
            "referral code. Refusing to draft outreach with a fallback link."
        ),
        "program": program,
    }


def bootstrap_auth_and_db(config: dict) -> dict:
    auth_path = select_auth_path(config)
    if auth_path == "setup_required":
        return {
            "status": "error",
            "error_code": "auth_setup_required",
            "message": "No Seren Desktop auth or SEREN_API_KEY was found.",
            "setup_url": config["auth"]["setup_url"],
        }

    return {
        "status": "ok",
        "auth_path": auth_path,
        "database_status": "ready",
        "database": config["database"],
    }


def bootstrap_affiliate_context(config: dict, *, gateway: Any | None = None) -> dict:
    failure = bool(config.get("simulate", {}).get("affiliate_bootstrap_failure", False))
    if failure:
        return {
            "status": "error",
            "error_code": "affiliate_bootstrap_failed",
            "affiliate_feed_status": "unavailable",
            "retry_count": 3,
            "fail_closed": True,
            "message": "Default affiliate program context failed three immediate bootstrap attempts.",
        }

    if gateway is None and bool(config.get("dry_run", True)):
        program = _select_seren_bucks_program(_dry_run_partner_links(config), config)
        if not program or not _is_real_srn_link(program["tracked_link"]):
            return _invalid_link_error(program, retry_count=0)
        return _success(
            config=config,
            program=program,
            registered_this_run=False,
            retry_count=0,
            affiliate_feed_status="dry_run",
        )

    client = gateway or SerenAffiliateGateway()
    last_error = ""
    for attempt in range(1, MAX_BOOTSTRAP_ATTEMPTS + 1):
        registered = False
        try:
            try:
                client.call(PUBLISHER, "GET", "/affiliates/me")
            except GatewayError as exc:
                if exc.status_code != 404:
                    raise
                client.call(PUBLISHER, "POST", "/affiliates", {})
                registered = True

            partner_links = client.call(PUBLISHER, "GET", "/affiliates/me/partner-links")
        except GatewayError as exc:
            last_error = str(exc)
            continue

        program = _select_seren_bucks_program(partner_links, config)
        if not program or not _is_real_srn_link(program["tracked_link"]):
            return _invalid_link_error(program, retry_count=attempt)
        return _success(
            config=config,
            program=program,
            registered_this_run=registered,
            retry_count=attempt,
            affiliate_feed_status="ready",
        )

    return {
        "status": "error",
        "error_code": "affiliate_bootstrap_failed",
        "affiliate_feed_status": "unavailable",
        "retry_count": MAX_BOOTSTRAP_ATTEMPTS,
        "fail_closed": True,
        "message": "Default affiliate program context failed three immediate bootstrap attempts.",
        "last_error": last_error,
    }
