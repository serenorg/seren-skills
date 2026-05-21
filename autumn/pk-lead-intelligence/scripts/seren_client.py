"""Thin wrapper for Seren publisher HTTP calls.

Pure Python over `urllib`, no third-party deps. The wrapper layers
cleanly so each piece is unit-testable in isolation:

- `resolve_api_key()` — pick the right env var.
- `_build_url()` — construct the gateway URL for a publisher + path.
- `_build_headers()` — Bearer auth + Content-Type for JSON bodies.
- `_decode_response()` — 2xx → dict, non-2xx → `PublisherError`.
- `call_publisher()` — orchestrates the four above plus a `fetcher`.

The default `fetcher` is a thin urllib wrapper. Tests inject a fake
fetcher so transport itself never runs in the unit suite — that
single live call is exercised end-to-end by the Phase 1 dry-run
checkpoint with the operator watching.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Callable, Optional


# (method, url, headers, body) -> (status, body_bytes)
Fetcher = Callable[
    [str, str, dict, Optional[bytes]],
    tuple[int, bytes],
]


_DEFAULT_BASE_URL = "https://api.serendb.com"
_API_KEY_ENV_VARS = ("API_KEY", "SEREN_API_KEY")


class PublisherError(RuntimeError):
    """Raised when a publisher call returns a non-2xx status or
    yields an unparseable 2xx body. Carries the HTTP status so
    callers can branch on it (e.g. auto-pause on 402).
    """

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------- #
# Env / api-key resolution                                              #
# --------------------------------------------------------------------- #


def resolve_api_key() -> str:
    """Return the Seren API key from env, preferring `API_KEY` over
    `SEREN_API_KEY`.

    Seren Desktop injects `API_KEY` for the lifetime of a run; an
    operator running standalone falls back to `SEREN_API_KEY`. The
    desktop-injected key wins when both are set so a desktop session
    is not accidentally routed at a stale standalone key.
    """

    for var in _API_KEY_ENV_VARS:
        value = os.environ.get(var)
        if value:
            return value
    raise RuntimeError(
        "Neither API_KEY nor SEREN_API_KEY is set — "
        "see https://docs.serendb.com/skills.md for setup"
    )


# --------------------------------------------------------------------- #
# Pure helpers                                                          #
# --------------------------------------------------------------------- #


def _build_url(
    publisher: str,
    path: str,
    base_url: str = _DEFAULT_BASE_URL,
) -> str:
    """Construct the gateway URL for `publisher` + `path`.

    Trailing slash on the slug and leading slash on the path are both
    tolerated; the function normalizes to exactly one slash between
    segments. Empty inputs are rejected — passing them through would
    produce a URL that hits the gateway root and confuses log triage.
    """

    if not publisher:
        raise ValueError("publisher slug must not be empty")
    if not path:
        raise ValueError("path must not be empty")

    publisher = publisher.rstrip("/")
    path = path.lstrip("/")
    return f"{base_url.rstrip('/')}/publishers/{publisher}/{path}"


def _build_headers(api_key: str, *, has_body: bool = False) -> dict[str, str]:
    """Return the HTTP headers for one publisher call.

    Always sets `Authorization: Bearer <key>`. Only attaches
    `Content-Type: application/json` when a body is present so GETs
    do not advertise a content type they are not sending.
    """

    if not api_key:
        raise ValueError("api_key must not be empty")

    headers = {"Authorization": f"Bearer {api_key}"}
    if has_body:
        headers["Content-Type"] = "application/json"
    return headers


def _decode_response(status: int, body: bytes) -> dict[str, Any]:
    """Decode a publisher response into a JSON dict.

    2xx with empty body returns `{}` (common for 204 No Content).
    2xx with non-JSON body raises `PublisherError` — callers
    expecting a structured response should not have to disambiguate
    a successful-but-garbled reply from a failed call.
    Non-2xx raises `PublisherError` with the body inlined into the
    message so the operator can fix it without re-running.
    """

    if 200 <= status < 300:
        if not body:
            return {}
        try:
            decoded = json.loads(body)
        except json.JSONDecodeError as exc:
            raise PublisherError(
                status,
                f"Publisher returned 2xx but body was not JSON: {exc}",
            ) from exc
        if not isinstance(decoded, dict):
            raise PublisherError(
                status,
                f"Publisher returned 2xx but body was not a JSON object: "
                f"{type(decoded).__name__}",
            )
        return decoded

    # Non-2xx — inline the body (truncated) so the operator can read
    # the gateway's complaint without re-running.
    text = body.decode("utf-8", errors="replace")
    if len(text) > 1000:
        text = text[:1000] + "...[truncated]"
    raise PublisherError(
        status,
        f"Publisher returned HTTP {status}: {text}",
    )


# Keys that mark the model-routing gateway envelope. When all of these
# co-occur inside `data`, the upstream provider's payload lives at
# `data.body`; without them, `data` IS the upstream payload (the shape
# data publishers like `seren-db` return).
_GATEWAY_ENVELOPE_MARKERS = {"status", "body"}


def _unwrap_gateway_envelope(payload: Any) -> Any:
    """Strip the Seren gateway wrapper from a decoded publisher response.

    Every gateway response is wrapped in `{"data": <X>}`. Model-routing
    publishers (`perplexity`, `seren-models`) further wrap the upstream
    payload as `{"data": {"status": 200, "cost": …, "body": <upstream>}}`.
    Data publishers (`seren-db`) put the payload directly at `data`.

    This helper returns the innermost upstream payload regardless of
    shape, so adapters can keep their `response.get("choices")` /
    `response.get("id")` access pattern without rewriting for the
    envelope. Anything that does not look like the gateway wrap is
    returned unchanged — preserves backwards-compat with fixtures and
    publishers that already return the payload at the top level.

    Trace: empty-research bug, May 2026 — every Note since inception
    had `choices=None` because the adapters read the outer envelope.
    """

    if not (isinstance(payload, dict) and list(payload.keys()) == ["data"]):
        return payload

    inner = payload["data"]
    if isinstance(inner, dict) and _GATEWAY_ENVELOPE_MARKERS.issubset(inner.keys()):
        return inner["body"]
    return inner


# --------------------------------------------------------------------- #
# Default urllib fetcher                                                #
# --------------------------------------------------------------------- #


def _default_fetcher(
    method: str,
    url: str,
    headers: dict[str, str],
    body: Optional[bytes],
) -> tuple[int, bytes]:
    """urllib-based fetcher. Used as the default in production.

    Not unit-tested — the Phase 1 dry-run checkpoint validates this
    against the live gateway. Kept tiny so it is obvious-by-reading
    rather than tested-by-mocking.
    """

    req = urllib.request.Request(url=url, method=method, headers=headers, data=body)
    try:
        with urllib.request.urlopen(req) as resp:  # noqa: S310 — trusted host
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        # urllib raises on non-2xx by default. Surface the body so
        # `_decode_response` can build a useful error message.
        return exc.code, exc.read()


# --------------------------------------------------------------------- #
# Public surface                                                        #
# --------------------------------------------------------------------- #


def call_publisher(
    publisher: str,
    method: str,
    path: str,
    *,
    body: Optional[dict] = None,
    api_key: Optional[str] = None,
    base_url: str = _DEFAULT_BASE_URL,
    fetcher: Optional[Fetcher] = None,
) -> Any:
    """Make one call to a Seren publisher and return the upstream
    payload after stripping the gateway envelope.

    The Seren gateway wraps responses in `{"data": …}`; model-routing
    publishers (`perplexity`, `seren-models`) wrap further as
    `{"data": {"status": …, "body": <upstream>, "cost": …}}`. This
    function unwraps both layers so callers can read the upstream
    payload directly (`response["choices"]`, `response["id"]`, etc.).
    See `_unwrap_gateway_envelope` for the precise rules.

    Returns `Any` because data publishers (`seren-db`) put a list at
    `data` while model-routing publishers put a dict at `data.body`.

    `api_key` defaults to `resolve_api_key()`. `fetcher` defaults to
    `_default_fetcher`. Both are exposed for tests and for unusual
    runtime configurations (e.g. an authenticated session that
    rotates keys mid-run).

    Non-2xx responses raise `PublisherError` with the HTTP status
    and (truncated) response body in the message.
    """

    if api_key is None:
        api_key = resolve_api_key()
    if fetcher is None:
        fetcher = _default_fetcher

    url = _build_url(publisher, path, base_url=base_url)
    headers = _build_headers(api_key, has_body=body is not None)
    encoded_body = (
        json.dumps(body).encode("utf-8") if body is not None else None
    )

    status, response_body = fetcher(method, url, headers, encoded_body)
    decoded = _decode_response(status, response_body)
    return _unwrap_gateway_envelope(decoded)
