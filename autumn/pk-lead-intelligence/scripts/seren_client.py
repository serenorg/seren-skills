"""Thin wrapper for Seren publisher HTTP calls.

Pure Python over `urllib`, no third-party deps. The wrapper layers
cleanly so each piece is unit-testable in isolation:

- `resolve_api_key()` — find an API key across env, `.env`, or
  auto-register a fresh agent account on cold start.
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
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable, Optional


# (method, url, headers, body) -> (status, body_bytes)
Fetcher = Callable[
    [str, str, dict, Optional[bytes]],
    tuple[int, bytes],
]


_DEFAULT_BASE_URL = "https://api.serendb.com"
_API_KEY_ENV_VARS = ("API_KEY", "SEREN_API_KEY")

# Default skill root: parent of `scripts/`. `.env` lives at the root.
# Tests override this via the `skill_root` argument on `resolve_api_key`.
_DEFAULT_SKILL_ROOT = Path(__file__).resolve().parent.parent

# Skill identifier sent to `POST /auth/agent` on first-run registration.
# Matches the skill's package name + the value documented in SKILL.md.
_SKILL_NAME = "pk-lead-intelligence"


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


# Cold-start error shown when no API key is available anywhere AND
# the caller opted out of auto-register (`auto_register=False`). Issue
# #792 — PR #790's message told Claude Cowork users to run
# `claude mcp add`, a Claude *Code* CLI command. Cowork is the desktop
# Claude app; its custom-connector install is a GUI flow. This message
# splits the two products into their own labelled blocks so the recipe
# matches the surface.
_MISSING_API_KEY_MESSAGE = (
    "Neither API_KEY nor SEREN_API_KEY is set — pk-lead-intelligence "
    "cannot reach the Seren publishers (perplexity, seren-models, "
    "google-drive, seren-cron, seren-db) without auth.\n\n"
    "Claude Cowork (desktop):\n"
    "  Open Claude Desktop, then Settings > Connectors > "
    "Add Custom Connector. Paste this URL:\n"
    "      https://mcp.serendb.com/mcp\n"
    "  Trigger any MCP call to complete OAuth. The hosted MCP "
    "exposes every publisher this skill calls.\n\n"
    "Claude Code (CLI):\n"
    "  claude mcp add --scope user --transport http seren "
    "https://mcp.serendb.com/mcp\n\n"
    "Locked-down host with no MCP path (CI, headless cron):\n"
    "  Register a Seren agent account and paste the returned "
    "key into <skill-root>/.env as SEREN_API_KEY=...\n"
    "    curl -sS -X POST https://api.serendb.com/auth/agent "
    '-H \'Content-Type: application/json\' '
    "-d '{\"name\":\"pk-lead-intelligence\"}'\n\n"
    "Reference: https://docs.serendb.com/skills.md"
)


def resolve_api_key(
    *,
    auto_register: bool = True,
    skill_root: Optional[Path] = None,
    fetcher: Optional[Fetcher] = None,
) -> str:
    """Return the Seren API key from any of four sources, in priority
    order:

      1. `os.environ["API_KEY"]` — Seren Desktop injection.
      2. `os.environ["SEREN_API_KEY"]` — operator-set in shell.
      3. `<skill-root>/.env` — `SEREN_API_KEY=...` line (the path
         SKILL.md tells users to paste into).
      4. If `auto_register=True` (default) — `POST /auth/agent`,
         write the returned key to `<skill-root>/.env`, return it.

    Issue #792: Jill on Claude Cowork should never see the cold-start
    error. With auto-register on, a brand-new Cowork user gets a
    fresh key without any manual step. The duplicate-account guard
    documented in SKILL.md is preserved because layer 3 catches any
    existing `.env` before layer 4 fires.

    `auto_register=False` is the legacy fail-fast behaviour, useful
    for CI/test code that wants the explicit error rather than a
    silent network call to `/auth/agent`.

    `skill_root` defaults to the directory containing `scripts/`.
    Tests override it to isolate `.env` reads and writes.

    `fetcher` defaults to the urllib-based `_default_fetcher`.
    Tests inject a fake to avoid hitting the network.
    """

    for var in _API_KEY_ENV_VARS:
        value = os.environ.get(var)
        if value:
            return value

    root = skill_root if skill_root is not None else _DEFAULT_SKILL_ROOT
    dotenv_key = _read_seren_api_key_from_dotenv(root)
    if dotenv_key:
        return dotenv_key

    if not auto_register:
        raise RuntimeError(_MISSING_API_KEY_MESSAGE)

    transport = fetcher if fetcher is not None else _default_fetcher
    registered_key = _register_new_agent_account(transport)
    _write_seren_api_key_to_dotenv(root, registered_key)
    print(
        f"pk-lead-intelligence: registered new Seren agent account. "
        f"Key written to {root / '.env'}. If you have an existing "
        f"funded account, set SEREN_API_KEY before next run.",
        file=sys.stderr,
    )
    return registered_key


def _parse_dotenv(path: Path) -> dict[str, str]:
    """Parse a simple `.env` file into an ordered `{KEY: value}` dict.

    Hand-rolled parser (no python-dotenv dependency at module import
    time): one `KEY=value` per line plus `#` comments and blank lines.
    Surrounding single/double quotes on the value are stripped. The
    first occurrence of a key wins. Anything beyond that (export
    prefixes, multiline values) is out of scope — the file is
    operator-edited and the skill writes it in the simple form.
    """

    result: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        result.setdefault(key, value.strip().strip('"').strip("'"))
    return result


def load_dotenv_into_environ(paths: Iterable[Path]) -> dict[str, str]:
    """Load every key from the first existing `.env` among `paths`
    into `os.environ`.

    The whole file is loaded — not just `SEREN_API_KEY` — so the
    1Password credential path (`OP_SERVICE_ACCOUNT_TOKEN`, `OP_VAULT`,
    `OP_ITEM`) and the Path-A `SF_*` vars resolve without the operator
    running `set -a; . ./.env` before launch (issue #848).

    A variable already present in `os.environ` is never overwritten:
    the real environment wins over `.env`, matching `resolve_api_key`'s
    precedence (env layers are checked before the `.env` fallback).
    Only the first existing file in `paths` is consulted. Returns the
    mapping of keys actually set, for logging / tests.
    """

    loaded: dict[str, str] = {}
    for path in paths:
        if not path.exists():
            continue
        for key, value in _parse_dotenv(path).items():
            if key in os.environ:
                continue
            os.environ[key] = value
            loaded[key] = value
        break
    return loaded


def _read_seren_api_key_from_dotenv(skill_root: Path) -> Optional[str]:
    """Read `SEREN_API_KEY` from `<skill-root>/.env` if present.

    Shares `_parse_dotenv` with `load_dotenv_into_environ` so the file
    format is parsed in exactly one place.
    """

    dotenv_path = skill_root / ".env"
    if not dotenv_path.exists():
        return None
    return _parse_dotenv(dotenv_path).get("SEREN_API_KEY") or None


def _register_new_agent_account(fetcher: Fetcher) -> str:
    """Call `POST /auth/agent` and return the freshly-issued key.

    Per docs.serendb.com/skills.md the endpoint returns
    `{"data": {"agent": {"api_key": "..."}}}` on success. Raised
    errors carry the gateway's body so the operator can act on it.
    """

    body = json.dumps({"name": _SKILL_NAME}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    url = f"{_DEFAULT_BASE_URL}/auth/agent"

    status, response_body = fetcher("POST", url, headers, body)
    if not (200 <= status < 300):
        text = response_body.decode("utf-8", errors="replace")
        if len(text) > 500:
            text = text[:500] + "...[truncated]"
        raise RuntimeError(
            f"pk-lead-intelligence: auto-register POST {url} returned "
            f"HTTP {status}: {text}\n\n{_MISSING_API_KEY_MESSAGE}"
        )

    try:
        decoded = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"pk-lead-intelligence: auto-register returned 2xx but "
            f"body was not JSON: {exc}"
        ) from exc

    api_key = (
        (decoded or {}).get("data", {}).get("agent", {}).get("api_key")
    )
    if not api_key:
        raise RuntimeError(
            "pk-lead-intelligence: auto-register response missing "
            "data.agent.api_key — endpoint contract may have changed; "
            "set SEREN_API_KEY manually."
        )
    return api_key


def _write_seren_api_key_to_dotenv(skill_root: Path, api_key: str) -> None:
    """Append `SEREN_API_KEY=<key>` to `<skill-root>/.env`.

    If the file does not exist, it is created with just this line.
    If the file exists but has no `SEREN_API_KEY=`, the line is
    appended. If the file already has the line — should not happen
    because the read layer catches it earlier — the existing line is
    rewritten so a stale empty value cannot shadow the new key.
    """

    dotenv_path = skill_root / ".env"
    new_line = f"SEREN_API_KEY={api_key}"

    if not dotenv_path.exists():
        dotenv_path.write_text(new_line + "\n", encoding="utf-8")
        return

    lines = dotenv_path.read_text(encoding="utf-8").splitlines()
    updated: list[str] = []
    found = False
    for line in lines:
        stripped = line.strip()
        if (
            stripped.startswith("SEREN_API_KEY=")
            or stripped.startswith("SEREN_API_KEY =")
        ):
            updated.append(new_line)
            found = True
        else:
            updated.append(line)
    if not found:
        updated.append(new_line)
    dotenv_path.write_text("\n".join(updated) + "\n", encoding="utf-8")


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
