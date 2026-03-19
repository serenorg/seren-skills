from __future__ import annotations

import json
import math
import os
import select
import shlex
import signal
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import pstdev
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

SEREN_POLYMARKET_PUBLISHER_HOST = "api.serendb.com"
SEREN_PUBLISHERS_PREFIX = "/publishers/"
SEREN_POLYMARKET_DATA_PUBLISHER = "polymarket-data"
SEREN_API_BASE = f"https://{SEREN_POLYMARKET_PUBLISHER_HOST}"
SEREN_POLYMARKET_DATA_URL_PREFIX = (
    f"{SEREN_API_BASE}{SEREN_PUBLISHERS_PREFIX}{SEREN_POLYMARKET_DATA_PUBLISHER}"
)
POLYMARKET_DATA_API_BASE_URL = "https://data-api.polymarket.com"
POLYMARKET_CLOB_BASE_URL = "https://clob.polymarket.com"
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_CHAIN_ID = 137
LIVE_SAFETY_VERSION = "2026-03-18.polymarket-live-safety-v3"
USDC_DECIMALS = 6

# Polymarket contract addresses on Polygon mainnet
POLYGON_USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
POLYGON_CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
POLYGON_NEG_RISK_CTF_EXCHANGE = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
POLYGON_NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"
POLYGON_CONDITIONAL_TOKENS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
ERC20_ALLOWANCE_ABI_FRAGMENT = "0xdd62ed3e"  # allowance(address,address)
ERC1155_IS_APPROVED_ABI_FRAGMENT = "0xe985e9c5"  # isApprovedForAll(address,address)


def maybe_load_dotenv(skill_root: Path) -> None:
    env_path = skill_root / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            key, sep, value = line.partition("=")
            if sep != "=":
                continue
            key = key.strip()
            if not key:
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            os.environ.setdefault(key, value)
        return
    load_dotenv(env_path, override=False)


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if isinstance(value, str):
            value = value.replace("$", "").replace(",", "").strip()
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def safe_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def parse_iso_ts(value: Any) -> int | None:
    raw = safe_str(value, "")
    if not raw:
        return None
    try:
        return int(datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def json_to_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return parsed
    return []


def normalize_history(
    raw_history: Any,
    start_ts: int | None = None,
    end_ts: int | None = None,
) -> list[tuple[int, float]]:
    rows: list[tuple[int, float]] = []
    seen: set[int] = set()
    if not isinstance(raw_history, list):
        return rows

    for item in raw_history:
        t = -1
        p = -1.0
        if isinstance(item, dict):
            t = safe_int(item.get("t"), -1)
            p = safe_float(item.get("p"), -1.0)
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            t = safe_int(item[0], -1)
            p = safe_float(item[1], -1.0)
        if t < 0 or not (0.0 <= p <= 1.0) or t in seen:
            continue
        if start_ts is not None and t < start_ts:
            continue
        if end_ts is not None and t > end_ts:
            continue
        seen.add(t)
        rows.append((t, p))

    rows.sort(key=lambda row: row[0])
    return rows


def history_volatility_bps(history: list[tuple[int, float]], window_points: int) -> float:
    window = max(2, window_points)
    if len(history) < window + 1:
        return 0.0
    moves = [
        abs((history[idx][1] - history[idx - 1][1]) * 10000.0)
        for idx in range(1, len(history))
    ]
    recent = moves[-window:]
    if len(recent) <= 1:
        return recent[0] if recent else 0.0
    return float(pstdev(recent))


def last_move_bps(history: list[tuple[int, float]]) -> float:
    if len(history) < 2:
        return 0.0
    return abs((history[-1][1] - history[-2][1]) * 10000.0)


def best_price(levels: Any, fallback: float = 0.0) -> float:
    if not isinstance(levels, list) or not levels:
        return fallback
    level = levels[0]
    if isinstance(level, dict):
        return safe_float(level.get("price"), fallback)
    if isinstance(level, (list, tuple)) and level:
        return safe_float(level[0], fallback)
    return fallback


def snap_price(price: float, tick_size: str, side: str) -> float:
    tick = safe_float(tick_size, 0.01)
    if tick <= 0:
        tick = 0.01
    normalized = clamp(price, tick, 1.0 - tick)
    if side.upper() == "BUY":
        snapped = math.floor(normalized / tick) * tick
    else:
        snapped = math.ceil(normalized / tick) * tick
    decimals = max(0, len(tick_size.split(".")[1]) if "." in tick_size else 0)
    return round(clamp(snapped, tick, 1.0 - tick), decimals)


def parse_midpoint_payload(payload: Any, fallback_mid: float = 0.0) -> float:
    if isinstance(payload, dict):
        for key in ("mid", "midpoint", "price"):
            value = safe_float(payload.get(key), -1.0)
            if 0.0 <= value <= 1.0:
                return value
    return fallback_mid


def parse_book_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {
            "best_bid": 0.0,
            "best_ask": 0.0,
            "tick_size": "0.01",
            "neg_risk": False,
            "raw": payload,
        }
    best_bid = best_price(payload.get("bids"), 0.0)
    best_ask = best_price(payload.get("asks"), 0.0)
    tick_size = safe_str(
        payload.get("tick_size", payload.get("minimum_tick_size", "0.01")),
        "0.01",
    )
    return {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "tick_size": tick_size,
        "neg_risk": bool(payload.get("neg_risk", False)),
        "raw": payload,
    }


def extract_token_id(raw_market: dict[str, Any]) -> str:
    token_ids = json_to_list(raw_market.get("clobTokenIds"))
    if token_ids:
        token_id = safe_str(token_ids[0], "")
        if token_id:
            return token_id
    return safe_str(raw_market.get("token_id"), safe_str(raw_market.get("market_id"), ""))


def extract_event_id(raw_market: dict[str, Any]) -> str:
    events = json_to_list(raw_market.get("events"))
    if events and isinstance(events[0], dict):
        event_id = safe_str(events[0].get("id"), "")
        if event_id:
            return event_id
    for key in ("event_id", "seriesSlug", "category"):
        value = safe_str(raw_market.get(key), "")
        if value:
            return value
    return "misc"


def infer_position_size(position: dict[str, Any]) -> float:
    for key in (
        "size",
        "amount",
        "quantity",
        "position",
        "balance",
        "shares",
        "outcomeTokens",
        "token_balance",
    ):
        value = safe_float(position.get(key), float("nan"))
        if not math.isnan(value):
            return value
    for key in ("amount", "balance", "position", "shares"):
        nested = position.get("available", {})
        if isinstance(nested, dict):
            value = safe_float(nested.get(key), float("nan"))
            if not math.isnan(value):
                return value
    return 0.0


def _rows_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
    return []


def positions_by_key(raw_positions: Any) -> dict[str, float]:
    rows = _rows_from_payload(raw_positions)
    out: dict[str, float] = {}
    for row in rows:
        size = infer_position_size(row)
        for key in (
            safe_str(row.get("asset_id"), ""),
            safe_str(row.get("token_id"), ""),
            safe_str(row.get("market"), ""),
            safe_str(row.get("market_id"), ""),
        ):
            if key:
                out[key] = size
    return out


def extract_cash_balance_usd(raw_balance: Any) -> float:
    if isinstance(raw_balance, dict):
        for key in (
            "available",
            "available_balance",
            "balance",
            "cash",
            "amount",
            "value",
        ):
            value = raw_balance.get(key)
            if value is not None:
                parsed = safe_float(value, float("nan"))
                if not math.isnan(parsed):
                    return max(0.0, parsed)
        for value in raw_balance.values():
            parsed = extract_cash_balance_usd(value)
            if parsed > 0.0:
                return parsed
    if isinstance(raw_balance, list):
        for item in raw_balance:
            parsed = extract_cash_balance_usd(item)
            if parsed > 0.0:
                return parsed
    return 0.0


def total_notional(exposure_by_key: dict[str, float]) -> float:
    return round(sum(max(0.0, safe_float(value, 0.0)) for value in exposure_by_key.values()), 4)


def _alarm_timeout_supported() -> bool:
    return hasattr(signal, "setitimer") and hasattr(signal, "SIGALRM")


def _call_with_timeout(
    operation_name: str,
    func: Any,
    *,
    timeout_seconds: float,
    retry_attempts: int,
) -> Any:
    attempts = max(0, retry_attempts) + 1
    last_error: Exception | None = None

    def _timeout_handler(signum: int, frame: Any) -> None:
        del signum, frame
        raise TimeoutError(f"{operation_name} timed out after {timeout_seconds:.2f}s")

    for attempt in range(attempts):
        previous_handler = None
        armed_timeout = False
        try:
            if timeout_seconds > 0 and _alarm_timeout_supported():
                previous_handler = signal.getsignal(signal.SIGALRM)
                signal.signal(signal.SIGALRM, _timeout_handler)
                signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
                armed_timeout = True
            return func()
        except TimeoutError as exc:
            last_error = exc
            if attempt + 1 >= attempts:
                break
        finally:
            if armed_timeout:
                signal.setitimer(signal.ITIMER_REAL, 0.0)
            if previous_handler is not None:
                signal.signal(signal.SIGALRM, previous_handler)

    if last_error is not None:
        raise last_error
    raise RuntimeError(f"{operation_name} failed without an explicit error.")


def _read_mcp_exact(fd: int, size: int, timeout_seconds: float) -> bytes:
    buf = bytearray()
    while len(buf) < size:
        ready, _, _ = select.select([fd], [], [], timeout_seconds)
        if not ready:
            raise TimeoutError("Timed out waiting for response from seren-mcp.")
        chunk = os.read(fd, size - len(buf))
        if not chunk:
            raise RuntimeError("seren-mcp closed stdout before completing a response.")
        buf.extend(chunk)
    return bytes(buf)


def _read_mcp_message(proc: subprocess.Popen[bytes], timeout_seconds: float) -> dict[str, Any]:
    if proc.stdout is None:
        raise RuntimeError("seren-mcp stdout is not available.")
    fd = proc.stdout.fileno()
    header_buf = bytearray()
    while b"\r\n\r\n" not in header_buf:
        header_buf.extend(_read_mcp_exact(fd, 1, timeout_seconds))
        if len(header_buf) > 16384:
            raise RuntimeError("Invalid MCP header: too large.")
    header_raw, _ = header_buf.split(b"\r\n\r\n", 1)
    headers: dict[str, str] = {}
    for line in header_raw.decode("ascii", errors="ignore").split("\r\n"):
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    content_length = safe_int(headers.get("content-length"), -1)
    if content_length < 0:
        raise RuntimeError("Invalid MCP header: missing content-length.")
    body = _read_mcp_exact(fd, content_length, timeout_seconds)
    parsed = json.loads(body.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise RuntimeError("Invalid MCP response payload.")
    return parsed


def _write_mcp_message(proc: subprocess.Popen[bytes], payload: dict[str, Any]) -> None:
    if proc.stdin is None:
        raise RuntimeError("seren-mcp stdin is not available.")
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    proc.stdin.write(header)
    proc.stdin.write(body)
    proc.stdin.flush()


def _mcp_request(
    proc: subprocess.Popen[bytes],
    request_id: int,
    method: str,
    params: dict[str, Any] | None,
    timeout_seconds: float,
) -> dict[str, Any]:
    request: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        request["params"] = params
    _write_mcp_message(proc, request)
    while True:
        message = _read_mcp_message(proc, timeout_seconds)
        if message.get("id") != request_id:
            continue
        error = message.get("error")
        if isinstance(error, dict):
            raise RuntimeError(safe_str(error.get("message"), "MCP request failed."))
        result = message.get("result")
        if isinstance(result, dict):
            return result
        return {"value": result}


def _extract_call_publisher_body(result: dict[str, Any]) -> Any:
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        body = structured.get("body")
        if isinstance(body, (dict, list)):
            return body
        return structured
    content = result.get("content")
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            if safe_str(item.get("type"), "") != "text":
                continue
            text = safe_str(item.get("text"), "")
            if not text:
                continue
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                body = parsed.get("body")
                if isinstance(body, (dict, list)):
                    return body
                return parsed
            if isinstance(parsed, list):
                return parsed
    body = result.get("body")
    if isinstance(body, (dict, list)):
        return body
    return result.get("value")


def call_publisher_json(
    publisher: str,
    method: str,
    path: str,
    headers: dict[str, str] | None = None,
    body: Any = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> Any:
    api_key = safe_str(os.getenv("API_KEY") or os.getenv("SEREN_API_KEY"), "").strip()
    prefer_mcp = not api_key

    if prefer_mcp:
        command_raw = safe_str(os.getenv("SEREN_MCP_COMMAND"), "seren-mcp").strip() or "seren-mcp"
        command = shlex.split(command_raw)
        if not command:
            raise RuntimeError("SEREN_MCP_COMMAND is empty.")
        proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            _mcp_request(
                proc=proc,
                request_id=1,
                method="initialize",
                params={
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "polymarket-live", "version": "1.0"},
                },
                timeout_seconds=timeout_seconds,
            )
            _write_mcp_message(
                proc,
                {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            )
            result = _mcp_request(
                proc=proc,
                request_id=2,
                method="tools/call",
                params={
                    "name": "call_publisher",
                    "arguments": {
                        "publisher": publisher,
                        "method": method.upper(),
                        "path": path,
                        "headers": headers or {},
                        "body": json.dumps(body) if body is not None else None,
                        "response_format": "json",
                    },
                },
                timeout_seconds=timeout_seconds,
            )
            return _extract_call_publisher_body(result)
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=1)

    req_headers = {"Accept": "application/json", "Authorization": f"Bearer {api_key}"}
    if headers:
        req_headers.update(headers)
    data = None
    if body is not None:
        req_headers["Content-Type"] = "application/json"
        data = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    request = Request(
        f"{SEREN_API_BASE}{SEREN_PUBLISHERS_PREFIX}{publisher}{path}",
        headers=req_headers,
        method=method.upper(),
        data=data,
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        text = response.read().decode("utf-8")
        if not text:
            return {}
        return json.loads(text)


def _call_clob_json(path: str, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> Any:
    request = Request(
        f"{POLYMARKET_CLOB_BASE_URL}{path}",
        headers={"Accept": "application/json", "User-Agent": "seren-polymarket-live/1.0"},
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        text = response.read().decode("utf-8")
        if not text:
            return {}
        return json.loads(text)


def fetch_trading_json(path: str, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> Any:
    return _call_clob_json(path=path, timeout_seconds=timeout_seconds)


def fetch_markets_page(
    *,
    limit: int,
    offset: int = 0,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    query = urlencode(
        {
            "active": "true",
            "closed": "false",
            "limit": limit,
            "offset": offset,
            "order": "volume24hr",
            "ascending": "false",
        }
    )
    payload = call_publisher_json(
        publisher=SEREN_POLYMARKET_DATA_PUBLISHER,
        method="GET",
        path=f"/markets?{query}",
        timeout_seconds=timeout_seconds,
    )
    return payload if isinstance(payload, list) else []


def fetch_history(
    *,
    token_id: str,
    interval: str = "max",
    fidelity_minutes: int = 60,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> list[tuple[int, float]]:
    query = urlencode(
        {
            "market": token_id,
            "interval": interval,
            "fidelity": max(1, fidelity_minutes),
        }
    )
    payload = _call_clob_json(
        path=f"/prices-history?{query}",
        timeout_seconds=timeout_seconds,
    )
    if not isinstance(payload, dict):
        return []
    return normalize_history(json_to_list(payload.get("history")))


def fetch_book(token_id: str, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> dict[str, Any]:
    payload = _call_clob_json(
        path=f"/book?{urlencode({'token_id': token_id})}",
        timeout_seconds=timeout_seconds,
    )
    return parse_book_payload(payload)


def fetch_midpoint(token_id: str, fallback_mid: float, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> float:
    payload = _call_clob_json(
        path=f"/midpoint?{urlencode({'token_id': token_id})}",
        timeout_seconds=timeout_seconds,
    )
    return parse_midpoint_payload(payload, fallback_mid=fallback_mid)


def fetch_fee_rate_bps(token_id: str, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> int:
    try:
        payload = _call_clob_json(
            path=f"/fee-rate?{urlencode({'token_id': token_id})}",
            timeout_seconds=timeout_seconds,
        )
    except Exception:
        return 0
    if isinstance(payload, dict):
        for key in ("fee_rate_bps", "feeRateBps", "rate_bps"):
            value = safe_int(payload.get(key), -1)
            if value >= 0:
                return value
    return 0


def load_live_single_markets(
    *,
    markets_max: int,
    min_seconds_to_resolution: int,
    volatility_window_points: int,
    min_history_points: int,
    min_liquidity_usd: float,
    markets_fetch_limit: int,
    history_interval: str = "max",
    history_fidelity_minutes: int = 60,
    default_rebate_bps: float = 0.0,
    shock_bps_threshold: float | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    now_ts = int(time.time())
    selected: list[dict[str, Any]] = []
    offset = 0
    seen_tokens: set[str] = set()

    while len(selected) < markets_max and offset < max(50, markets_fetch_limit):
        page = fetch_markets_page(
            limit=min(100, max(markets_fetch_limit, markets_max * 5)),
            offset=offset,
            timeout_seconds=timeout_seconds,
        )
        if not page:
            break
        for raw_market in page:
            if not isinstance(raw_market, dict):
                continue
            token_id = extract_token_id(raw_market)
            if not token_id or token_id in seen_tokens:
                continue
            seen_tokens.add(token_id)

            liquidity = safe_float(raw_market.get("liquidity"), 0.0)
            if liquidity < min_liquidity_usd:
                continue

            end_ts = parse_iso_ts(raw_market.get("endDate")) or safe_int(raw_market.get("end_ts"), 0)
            ttl = max(0, end_ts - now_ts)
            if ttl < min_seconds_to_resolution:
                continue

            history = fetch_history(
                token_id=token_id,
                interval=history_interval,
                fidelity_minutes=history_fidelity_minutes,
                timeout_seconds=timeout_seconds,
            )
            if len(history) < min_history_points:
                continue

            fallback_mid = history[-1][1]
            book = fetch_book(token_id, timeout_seconds=timeout_seconds)
            midpoint = fetch_midpoint(token_id, fallback_mid=fallback_mid, timeout_seconds=timeout_seconds)
            best_bid = safe_float(book.get("best_bid"), 0.0)
            best_ask = safe_float(book.get("best_ask"), 0.0)
            if not (0.0 <= best_bid <= 1.0 and 0.0 <= best_ask <= 1.0 and best_bid <= best_ask):
                continue

            volatility_bps = history_volatility_bps(history, volatility_window_points)
            shock_score = 0.0
            if shock_bps_threshold is not None and shock_bps_threshold > 0:
                shock_score = clamp(last_move_bps(history) / shock_bps_threshold, 0.0, 1.0)

            market_id = safe_str(raw_market.get("id"), token_id)
            selected.append(
                {
                    "market_id": market_id,
                    "question": safe_str(raw_market.get("question"), market_id),
                    "token_id": token_id,
                    "mid_price": round(midpoint, 4),
                    "best_bid": round(best_bid, 4),
                    "best_ask": round(best_ask, 4),
                    "seconds_to_resolution": ttl,
                    "volatility_bps": round(volatility_bps, 3),
                    "rebate_bps": round(
                        safe_float(raw_market.get("rebate_bps"), default_rebate_bps),
                        3,
                    ),
                    "tick_size": safe_str(book.get("tick_size"), "0.01"),
                    "neg_risk": bool(book.get("neg_risk", False)),
                    "news_shock_score": round(shock_score, 4),
                    "breaking_news": False,
                    "liquidity": round(liquidity, 4),
                }
            )
            if len(selected) >= markets_max:
                break

        if len(page) < min(100, max(markets_fetch_limit, markets_max * 5)):
            break
        offset += len(page)

    return selected


def load_live_pair_markets(
    *,
    pairs_max: int,
    min_seconds_to_resolution: int,
    min_history_points: int,
    min_liquidity_usd: float,
    markets_fetch_page_size: int,
    max_markets: int,
    history_interval: str = "max",
    history_fidelity_minutes: int = 60,
    default_rebate_bps: float = 0.0,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    now_ts = int(time.time())
    candidates: list[dict[str, Any]] = []
    offset = 0
    seen_tokens: set[str] = set()

    while len(candidates) < max_markets:
        page = fetch_markets_page(
            limit=min(200, max(25, markets_fetch_page_size)),
            offset=offset,
            timeout_seconds=timeout_seconds,
        )
        if not page:
            break
        for raw_market in page:
            if not isinstance(raw_market, dict):
                continue
            token_id = extract_token_id(raw_market)
            if not token_id or token_id in seen_tokens:
                continue
            seen_tokens.add(token_id)
            liquidity = safe_float(raw_market.get("liquidity"), 0.0)
            if liquidity < min_liquidity_usd:
                continue
            end_ts = parse_iso_ts(raw_market.get("endDate")) or safe_int(raw_market.get("end_ts"), 0)
            ttl = max(0, end_ts - now_ts)
            if ttl < min_seconds_to_resolution:
                continue
            history = fetch_history(
                token_id=token_id,
                interval=history_interval,
                fidelity_minutes=history_fidelity_minutes,
                timeout_seconds=timeout_seconds,
            )
            if len(history) < min_history_points:
                continue
            fallback_mid = history[-1][1]
            book = fetch_book(token_id, timeout_seconds=timeout_seconds)
            midpoint = fetch_midpoint(token_id, fallback_mid=fallback_mid, timeout_seconds=timeout_seconds)
            candidates.append(
                {
                    "market_id": safe_str(raw_market.get("id"), token_id),
                    "question": safe_str(raw_market.get("question"), token_id),
                    "event_id": extract_event_id(raw_market),
                    "token_id": token_id,
                    "end_ts": end_ts,
                    "seconds_to_resolution": ttl,
                    "mid_price": round(midpoint, 4),
                    "best_bid": round(safe_float(book.get("best_bid"), 0.0), 4),
                    "best_ask": round(safe_float(book.get("best_ask"), 0.0), 4),
                    "tick_size": safe_str(book.get("tick_size"), "0.01"),
                    "neg_risk": bool(book.get("neg_risk", False)),
                    "rebate_bps": round(
                        safe_float(raw_market.get("rebate_bps"), default_rebate_bps),
                        3,
                    ),
                    "volume24hr": safe_float(raw_market.get("volume24hr"), 0.0),
                    "history": history,
                }
            )
            if len(candidates) >= max_markets:
                break
        if len(page) < min(200, max(25, markets_fetch_page_size)):
            break
        offset += len(page)

    grouped: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        grouped.setdefault(candidate["event_id"], []).append(candidate)

    pairs: list[dict[str, Any]] = []
    for group in grouped.values():
        if len(group) < 2:
            continue
        ranked = sorted(group, key=lambda row: row["volume24hr"], reverse=True)
        for idx in range(len(ranked) - 1):
            primary = ranked[idx]
            secondary = ranked[idx + 1]
            index_secondary = {ts: px for ts, px in secondary["history"]}
            basis_series = [
                (px - index_secondary[ts]) * 10000.0
                for ts, px in primary["history"]
                if ts in index_secondary
            ]
            if len(basis_series) < min_history_points:
                continue
            pairs.append(
                {
                    "market_id": primary["market_id"],
                    "pair_market_id": secondary["market_id"],
                    "question": primary["question"],
                    "pair_question": secondary["question"],
                    "token_id": primary["token_id"],
                    "pair_token_id": secondary["token_id"],
                    "mid_price": primary["mid_price"],
                    "pair_mid_price": secondary["mid_price"],
                    "best_bid": primary["best_bid"],
                    "best_ask": primary["best_ask"],
                    "pair_best_bid": secondary["best_bid"],
                    "pair_best_ask": secondary["best_ask"],
                    "tick_size": primary["tick_size"],
                    "pair_tick_size": secondary["tick_size"],
                    "neg_risk": primary["neg_risk"],
                    "pair_neg_risk": secondary["neg_risk"],
                    "seconds_to_resolution": min(
                        primary["seconds_to_resolution"],
                        secondary["seconds_to_resolution"],
                    ),
                    "rebate_bps": round(
                        (primary["rebate_bps"] + secondary["rebate_bps"]) / 2.0,
                        3,
                    ),
                    "basis_volatility_bps": round(pstdev(basis_series[-min_history_points:]), 3),
                }
            )

    pairs.sort(
        key=lambda row: abs(
            (safe_float(row.get("mid_price"), 0.0) - safe_float(row.get("pair_mid_price"), 0.0))
            * 10000.0
        ),
        reverse=True,
    )
    return pairs[:pairs_max]


@dataclass
class LiveExecutionSettings:
    poll_attempts: int = 2
    poll_interval_seconds: float = 1.5
    cancel_before_requote: bool = True
    cancel_on_error: bool = True
    cycle_timeout_seconds: float = 45.0
    operation_timeout_seconds: float = 10.0
    operation_retry_attempts: int = 1
    min_cash_reserve_usd: float = 0.0
    max_live_drawdown_usd: float = 20.0
    max_live_drawdown_pct: float = 20.0
    prior_peak_equity_usd: float = 0.0
    runtime_version: str = LIVE_SAFETY_VERSION


class PolymarketPublisherTrader:
    def __init__(
        self,
        *,
        skill_root: Path,
        client_name: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        maybe_load_dotenv(skill_root)
        self.client_name = client_name
        self.timeout_seconds = timeout_seconds
        self._nonce = int(time.time() * 1000)

        try:
            from py_clob_client.clob_types import ApiCreds, OrderArgs, RequestArgs
            from py_clob_client.headers.headers import create_level_2_headers
            from py_clob_client.order_builder.builder import OrderBuilder
            from py_clob_client.signer import Signer
        except ImportError as exc:
            raise RuntimeError(
                "Live Polymarket execution requires `py-clob-client`. "
                "Create and activate a virtual environment first, then run "
                "`python -m pip install -r requirements.txt`."
            ) from exc

        private_key = safe_str(
            os.getenv("POLY_PRIVATE_KEY") or os.getenv("WALLET_PRIVATE_KEY"),
            "",
        ).strip()
        api_key = safe_str(os.getenv("POLY_API_KEY"), "").strip()
        api_passphrase = safe_str(os.getenv("POLY_PASSPHRASE"), "").strip()
        api_secret = safe_str(os.getenv("POLY_SECRET"), "").strip()
        if not private_key:
            raise RuntimeError(
                "Live Polymarket execution requires `POLY_PRIVATE_KEY` or `WALLET_PRIVATE_KEY` "
                "in addition to POLY_API_KEY/POLY_PASSPHRASE/POLY_SECRET."
            )
        if not api_key or not api_passphrase or not api_secret:
            raise RuntimeError(
                "Missing required Polymarket L2 credentials. Set "
                "`POLY_API_KEY`, `POLY_PASSPHRASE`, and `POLY_SECRET`."
            )

        chain_id = safe_int(os.getenv("POLY_CHAIN_ID"), DEFAULT_CHAIN_ID)
        signature_type = os.getenv("POLY_SIGNATURE_TYPE")
        funder = safe_str(os.getenv("POLY_FUNDER"), "").strip() or None

        self._api_creds = ApiCreds(
            api_key=api_key,
            api_secret=api_secret,
            api_passphrase=api_passphrase,
        )
        self._request_args_type = RequestArgs
        self._create_level_2_headers = create_level_2_headers
        self._order_args_type = OrderArgs
        self._order_builder = OrderBuilder(
            Signer(private_key, chain_id),
            sig_type=safe_int(signature_type, None) if signature_type else None,
            funder=funder,
        )
        self.address = self._order_builder.signer.address()

    def _signed_headers(self, method: str, path: str, body: Any = None) -> dict[str, str]:
        serialized = None
        if body is not None:
            serialized = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
        request_args = self._request_args_type(
            method=method.upper(),
            request_path=path,
            body=body,
            serialized_body=serialized,
        )
        return self._create_level_2_headers(
            self._order_builder.signer,
            self._api_creds,
            request_args,
        )

    def _call(self, method: str, path: str, body: Any = None) -> Any:
        req_headers = {
            "Accept": "application/json",
            "User-Agent": "seren-polymarket-live/1.0",
        }
        req_headers.update(self._signed_headers(method, path, body=body))
        data = None
        if body is not None:
            req_headers["Content-Type"] = "application/json"
            data = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        request = Request(
            f"{POLYMARKET_CLOB_BASE_URL}{path}",
            headers=req_headers,
            method=method.upper(),
            data=data,
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            text = response.read().decode("utf-8")
            if not text:
                return {}
            return json.loads(text)

    def next_nonce(self) -> int:
        self._nonce += 1
        return self._nonce

    def create_order(
        self,
        *,
        token_id: str,
        side: str,
        price: float,
        size: float,
        tick_size: str,
        neg_risk: bool,
        fee_rate_bps: int,
    ) -> dict[str, Any]:
        from py_clob_client.clob_types import CreateOrderOptions

        signed_order = self._order_builder.create_order(
            self._order_args_type(
                token_id=token_id,
                price=price,
                size=size,
                side=side.upper(),
                fee_rate_bps=fee_rate_bps,
                nonce=self.next_nonce(),
                expiration=0,
            ),
            CreateOrderOptions(
                tick_size=tick_size,
                neg_risk=neg_risk,
            ),
        )
        body = {
            "order": signed_order.dict(),
            "owner": self._api_creds.api_key,
            "orderType": "GTC",
            "postOnly": True,
        }
        return self._call("POST", "/order", body=body)

    def cancel_all(self) -> Any:
        return self._call("DELETE", "/cancel-all")

    def get_orders(self) -> Any:
        return self._call("GET", "/orders")

    def get_positions(self) -> Any:
        return self._call("GET", "/positions")


def check_neg_risk_approvals(
    wallet_address: str,
    *,
    rpc_url: str = "https://polygon-rpc.com",
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    """Check if wallet has USDC.e and CT approvals for NegRiskAdapter.

    Returns a dict with approval status and actionable error messages.
    Does not revert or block — callers decide how to handle missing approvals.
    """
    results: dict[str, Any] = {
        "wallet": wallet_address,
        "neg_risk_adapter": POLYGON_NEG_RISK_ADAPTER,
        "usdc_approved": False,
        "ct_approved": False,
        "checks_passed": False,
        "errors": [],
    }

    wallet_padded = wallet_address.lower().replace("0x", "").zfill(64)
    adapter_padded = POLYGON_NEG_RISK_ADAPTER.lower().replace("0x", "").zfill(64)

    def _eth_call(to: str, data: str) -> str:
        payload = json.dumps({
            "jsonrpc": "2.0", "method": "eth_call", "id": 1,
            "params": [{"to": to, "data": data}, "latest"],
        }).encode()
        req = Request(rpc_url, data=payload, headers={"Content-Type": "application/json"})
        try:
            with urlopen(req, timeout=timeout_seconds) as resp:
                body = json.loads(resp.read())
                return safe_str(body.get("result"), "0x0")
        except Exception:
            return "0x0"

    # Check USDC.e allowance to NegRiskAdapter
    usdc_data = f"0x{ERC20_ALLOWANCE_ABI_FRAGMENT[2:]}{wallet_padded}{adapter_padded}"
    usdc_result = _eth_call(POLYGON_USDC_E, usdc_data)
    try:
        usdc_allowance = int(usdc_result, 16)
    except (ValueError, TypeError):
        usdc_allowance = 0
    results["usdc_allowance_raw"] = usdc_allowance
    results["usdc_approved"] = usdc_allowance > 10 ** USDC_DECIMALS  # > 1 USDC.e

    # Check CT isApprovedForAll to NegRiskAdapter
    ct_data = f"0x{ERC1155_IS_APPROVED_ABI_FRAGMENT[2:]}{wallet_padded}{adapter_padded}"
    ct_result = _eth_call(POLYGON_CONDITIONAL_TOKENS, ct_data)
    try:
        ct_approved = int(ct_result, 16) > 0
    except (ValueError, TypeError):
        ct_approved = False
    results["ct_approved"] = ct_approved

    if not results["usdc_approved"]:
        results["errors"].append(
            f"USDC.e ({POLYGON_USDC_E}) is not approved for NegRiskAdapter "
            f"({POLYGON_NEG_RISK_ADAPTER}). Neg-risk market orders will fail with "
            f"'not enough balance / allowance'. Run: approve(NegRiskAdapter, MAX_UINT256) "
            f"on USDC.e contract."
        )
    if not results["ct_approved"]:
        results["errors"].append(
            f"Conditional Tokens ({POLYGON_CONDITIONAL_TOKENS}) setApprovalForAll is not "
            f"set for NegRiskAdapter ({POLYGON_NEG_RISK_ADAPTER}). Neg-risk SELL orders "
            f"will fail. Run: setApprovalForAll(NegRiskAdapter, true) on CT contract."
        )
    results["checks_passed"] = results["usdc_approved"] and results["ct_approved"]
    return results


class DirectClobTrader:
    """Direct Polymarket CLOB client for local py-clob-client execution."""

    def __init__(
        self,
        *,
        skill_root: Path,
        client_name: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        maybe_load_dotenv(skill_root)
        self.client_name = client_name
        self.timeout_seconds = timeout_seconds

        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import ApiCreds
        except ImportError as exc:
            raise RuntimeError(
                "Direct CLOB trading requires `py-clob-client`. "
                "Create and activate a virtual environment first, then run "
                "`python -m pip install -r requirements.txt`."
            ) from exc

        private_key = safe_str(
            os.getenv("POLY_PRIVATE_KEY") or os.getenv("WALLET_PRIVATE_KEY"),
            "",
        ).strip()
        api_key = safe_str(os.getenv("POLY_API_KEY"), "").strip()
        api_passphrase = safe_str(os.getenv("POLY_PASSPHRASE"), "").strip()
        api_secret = safe_str(os.getenv("POLY_SECRET"), "").strip()
        if not private_key:
            raise RuntimeError(
                "Direct CLOB trading requires `POLY_PRIVATE_KEY` or `WALLET_PRIVATE_KEY`."
            )
        if not api_key or not api_passphrase or not api_secret:
            raise RuntimeError(
                "Missing required Polymarket L2 credentials. Set "
                "`POLY_API_KEY`, `POLY_PASSPHRASE`, and `POLY_SECRET`."
            )

        chain_id = safe_int(os.getenv("POLY_CHAIN_ID"), DEFAULT_CHAIN_ID)
        creds = ApiCreds(
            api_key=api_key,
            api_secret=api_secret,
            api_passphrase=api_passphrase,
        )
        self._client = ClobClient(
            host="https://clob.polymarket.com",
            key=private_key,
            chain_id=chain_id,
            creds=creds,
        )
        self.address = safe_str(self._client.get_address(), "").lower()
        self._neg_risk_checked = False
        self._neg_risk_approval_status: dict[str, Any] | None = None

    def preflight_neg_risk(self) -> dict[str, Any]:
        """Check NegRiskAdapter approvals. Caches result for the session."""
        if self._neg_risk_checked:
            return self._neg_risk_approval_status or {"checks_passed": True}
        self._neg_risk_checked = True
        try:
            self._neg_risk_approval_status = check_neg_risk_approvals(self.address)
        except Exception as exc:
            self._neg_risk_approval_status = {
                "checks_passed": False,
                "errors": [f"Approval check failed: {exc}"],
            }
        return self._neg_risk_approval_status

    def create_order(
        self,
        *,
        token_id: str,
        side: str,
        price: float,
        size: float,
        tick_size: str,
        neg_risk: bool,
        fee_rate_bps: int,
    ) -> Any:
        del tick_size, neg_risk, fee_rate_bps
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY, SELL

        clob_side = BUY if side.upper() == "BUY" else SELL
        order_args = OrderArgs(
            price=price,
            size=size,
            side=clob_side,
            token_id=token_id,
        )
        signed_order = self._client.create_order(order_args)
        return self._client.post_order(signed_order, OrderType.GTC)

    def cancel_all(self) -> Any:
        return self._client.cancel_all()

    def get_orders(self) -> Any:
        return self._client.get_orders()

    def get_positions(self) -> Any:
        try:
            if not self.address:
                return []
            query = urlencode({"user": self.address})
            request = Request(
                f"{POLYMARKET_DATA_API_BASE_URL}/positions?{query}",
                headers={
                    "Accept": "application/json",
                    "User-Agent": f"{self.client_name}/{LIVE_SAFETY_VERSION}",
                },
            )
            with urlopen(request, timeout=self.timeout_seconds) as response:
                text = response.read().decode("utf-8")
            if not text:
                return []
            payload = json.loads(text)
            return payload if isinstance(payload, (dict, list)) else []
        except Exception:
            return []

    def cancel_order(self, order_id: str) -> Any:
        return self._client.cancel(order_id=order_id)

    def get_cash_balance(self) -> float:
        try:
            from py_clob_client.clob_types import AssetType, BalanceAllowanceParams

            payload = self._client.get_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            )
            raw = extract_cash_balance_usd(payload)
            if raw > 1_000_000:
                return raw / (10**USDC_DECIMALS)
            return raw
        except Exception as exc:
            raise RuntimeError(f"Unable to fetch collateral balance: {exc}") from exc


DEFAULT_UNWIND_BEFORE_RESOLUTION_SECONDS = 7 * 24 * 3600  # 7 days
DEFAULT_STALE_ORDER_MAX_AGE_SECONDS = 1800  # 30 minutes


def inject_held_position_markets(
    *,
    raw_positions: Any,
    markets: list[dict[str, Any]],
    default_rebate_bps: float = 0.0,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    unwind_before_resolution_seconds: int = DEFAULT_UNWIND_BEFORE_RESOLUTION_SECONDS,
) -> list[dict[str, Any]]:
    """Inject markets for held positions missing from the discovery list.

    Without this, the bot never generates SELL orders for tokens it already
    owns because those markets are not in the quote cycle.

    Markets within ``unwind_before_resolution_seconds`` of their endDate are
    tagged ``sell_only=True`` so the quoting engine only emits SELL orders,
    forcing the position to unwind before resolution.
    """
    sizes = positions_by_key(raw_positions)
    if not sizes:
        return markets

    now_ts = int(time.time())

    # Tag existing markets with sell_only if approaching resolution and user holds position
    updated_markets: list[dict[str, Any]] = []
    existing_tokens: set[str] = set()
    for market in markets:
        token_id = safe_str(market.get("token_id"), "")
        market_id = safe_str(market.get("market_id"), "")
        existing_tokens.add(token_id)
        existing_tokens.add(market_id)
        held = sizes.get(token_id, sizes.get(market_id, 0.0))
        seconds_to_res = max(0, safe_int(market.get("seconds_to_resolution"), 999999))
        if held > 0 and 0 < seconds_to_res < unwind_before_resolution_seconds:
            market = {**market, "sell_only": True, "source": market.get("source", "resolution-unwind")}
        updated_markets.append(market)
    existing_tokens.discard("")

    injected: list[dict[str, Any]] = []
    for token_id, shares in sizes.items():
        if shares <= 0 or token_id in existing_tokens:
            continue
        try:
            book = fetch_book(token_id, timeout_seconds=timeout_seconds)
            best_bid = safe_float(book.get("best_bid"), 0.0)
            best_ask = safe_float(book.get("best_ask"), 0.0)
            if not (0.0 < best_bid <= 1.0 and 0.0 < best_ask <= 1.0):
                continue
            midpoint = fetch_midpoint(token_id, fallback_mid=(best_bid + best_ask) / 2.0, timeout_seconds=timeout_seconds)
            end_ts = safe_int(book.get("end_date_iso"), 0) or safe_int(book.get("end_ts"), 0)
            seconds_to_res = max(0, end_ts - now_ts) if end_ts else 999999
            injected.append(
                {
                    "market_id": token_id,
                    "question": f"held-position:{token_id[:12]}",
                    "token_id": token_id,
                    "mid_price": round(midpoint, 4),
                    "best_bid": round(best_bid, 4),
                    "best_ask": round(best_ask, 4),
                    "seconds_to_resolution": seconds_to_res,
                    "volatility_bps": round(abs(best_ask - best_bid) * 10000.0, 3),
                    "rebate_bps": default_rebate_bps,
                    "tick_size": safe_str(book.get("tick_size"), "0.01"),
                    "neg_risk": bool(book.get("neg_risk", False)),
                    "sell_only": seconds_to_res < unwind_before_resolution_seconds,
                    "source": "held-inventory-unwind" if seconds_to_res < unwind_before_resolution_seconds else "held-position-injection",
                }
            )
            existing_tokens.add(token_id)
        except Exception:
            continue

    if injected:
        return updated_markets + injected
    return updated_markets


def single_market_inventory_notional(
    *,
    raw_positions: Any,
    markets: list[dict[str, Any]],
) -> dict[str, float]:
    sizes = positions_by_key(raw_positions)
    out: dict[str, float] = {}
    for market in markets:
        market_id = safe_str(market.get("market_id"), "")
        token_id = safe_str(market.get("token_id"), market_id)
        shares = sizes.get(token_id, sizes.get(market_id, 0.0))
        out[market_id] = round(shares * safe_float(market.get("mid_price"), 0.0), 4)
    return out


def pair_leg_exposure_notional(
    *,
    raw_positions: Any,
    markets: list[dict[str, Any]],
) -> dict[str, float]:
    sizes = positions_by_key(raw_positions)
    out: dict[str, float] = {}
    for market in markets:
        for market_id_key, token_key, price_key in (
            ("market_id", "token_id", "mid_price"),
            ("pair_market_id", "pair_token_id", "pair_mid_price"),
        ):
            market_id = safe_str(market.get(market_id_key), "")
            token_id = safe_str(market.get(token_key), market_id)
            shares = sizes.get(token_id, sizes.get(market_id, 0.0))
            out[market_id] = round(shares * safe_float(market.get(price_key), 0.0), 4)
    return out


def active_order_ids(raw_orders: Any) -> list[str]:
    rows = _rows_from_payload(raw_orders)
    out: list[str] = []
    for row in rows:
        for key in ("id", "orderID", "order_id"):
            value = safe_str(row.get(key), "")
            if value:
                out.append(value)
                break
    return out


def live_settings_from_execution(execution: dict[str, Any]) -> LiveExecutionSettings:
    return LiveExecutionSettings(
        poll_attempts=max(1, safe_int(execution.get("poll_attempts"), 2)),
        poll_interval_seconds=max(0.0, safe_float(execution.get("poll_interval_seconds"), 1.5)),
        cancel_before_requote=bool(execution.get("cancel_before_requote", True)),
        cancel_on_error=bool(execution.get("cancel_on_error", True)),
        cycle_timeout_seconds=max(0.0, safe_float(execution.get("cycle_timeout_seconds"), 45.0)),
        operation_timeout_seconds=max(0.0, safe_float(execution.get("operation_timeout_seconds"), 10.0)),
        operation_retry_attempts=max(0, safe_int(execution.get("operation_retry_attempts"), 1)),
        min_cash_reserve_usd=max(0.0, safe_float(execution.get("min_cash_reserve_usd"), 0.0)),
        max_live_drawdown_usd=max(0.0, safe_float(execution.get("max_live_drawdown_usd"), 0.0)),
        max_live_drawdown_pct=max(0.0, safe_float(execution.get("max_live_drawdown_pct"), 0.0)),
        prior_peak_equity_usd=max(0.0, safe_float(execution.get("prior_peak_equity_usd"), 0.0)),
    )


def _check_cycle_deadline(
    *,
    started_at: float,
    execution_settings: LiveExecutionSettings,
    stage: str,
) -> None:
    timeout_seconds = execution_settings.cycle_timeout_seconds
    if timeout_seconds <= 0:
        return
    elapsed = time.monotonic() - started_at
    if elapsed > timeout_seconds:
        raise TimeoutError(
            f"live_cycle_timeout: exceeded {timeout_seconds:.2f}s during {stage}"
        )


def _invoke_trader_call(
    operation_name: str,
    func: Any,
    execution_settings: LiveExecutionSettings,
) -> Any:
    return _call_with_timeout(
        operation_name,
        func,
        timeout_seconds=execution_settings.operation_timeout_seconds,
        retry_attempts=execution_settings.operation_retry_attempts,
    )


def _capture_live_risk(
    *,
    trader: Any,
    exposure_by_key: dict[str, float],
    execution_settings: LiveExecutionSettings,
) -> dict[str, Any]:
    requires_cash = (
        execution_settings.min_cash_reserve_usd > 0.0
        or execution_settings.max_live_drawdown_usd > 0.0
        or execution_settings.max_live_drawdown_pct > 0.0
        or execution_settings.prior_peak_equity_usd > 0.0
    )
    cash_balance_usd = 0.0
    cash_balance_error = ""

    if hasattr(trader, "get_cash_balance"):
        try:
            cash_balance_usd = max(0.0, safe_float(trader.get_cash_balance(), 0.0))
        except Exception as exc:
            cash_balance_error = str(exc)
    elif requires_cash:
        cash_balance_error = "live_cash_balance_unsupported"

    inventory_notional_usd = total_notional(exposure_by_key)
    current_equity_usd = round(cash_balance_usd + inventory_notional_usd, 4)
    peak_equity_usd = round(
        max(current_equity_usd, execution_settings.prior_peak_equity_usd),
        4,
    )
    drawdown_usd = round(max(0.0, peak_equity_usd - current_equity_usd), 4)
    drawdown_pct = round(
        ((drawdown_usd / peak_equity_usd) * 100.0) if peak_equity_usd > 0 else 0.0,
        4,
    )
    state = {
        "runtime_version": execution_settings.runtime_version,
        "cash_balance_usd": round(cash_balance_usd, 4),
        "inventory_notional_usd": round(inventory_notional_usd, 4),
        "current_equity_usd": current_equity_usd,
        "peak_equity_usd": peak_equity_usd,
        "drawdown_usd": drawdown_usd,
        "drawdown_pct": drawdown_pct,
    }

    if requires_cash and cash_balance_error:
        return {
            "status": "error",
            "error_code": "live_cash_balance_unavailable",
            "message": cash_balance_error,
            "state": state,
        }
    if (
        execution_settings.max_live_drawdown_usd > 0.0
        and drawdown_usd >= execution_settings.max_live_drawdown_usd
    ) or (
        execution_settings.max_live_drawdown_pct > 0.0
        and drawdown_pct >= execution_settings.max_live_drawdown_pct
    ):
        return {
            "status": "error",
            "error_code": "live_drawdown_limit_breached",
            "message": "Live drawdown limit breached. Trading halted and outstanding orders were cancelled.",
            "state": state,
        }
    return {"status": "ok", "state": state}


def _live_failure_payload(
    *,
    trader: Any,
    execution_settings: LiveExecutionSettings,
    error_code: str,
    message: str,
    cancel_response: Any,
    orders_submitted: list[dict[str, Any]],
    order_skips: list[dict[str, Any]],
    live_risk: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cleanup_cancel_all = None
    cleanup_error = ""
    if execution_settings.cancel_on_error:
        try:
            cleanup_cancel_all = _call_with_timeout(
                "cancel_all_cleanup",
                trader.cancel_all,
                timeout_seconds=max(1.0, execution_settings.operation_timeout_seconds),
                retry_attempts=0,
            )
        except Exception as exc:
            cleanup_error = str(exc)
    payload = {
        "status": "error",
        "error_code": error_code,
        "message": message,
        "cancel_all": cancel_response,
        "orders_submitted": orders_submitted,
        "order_skips": order_skips,
        "runtime_version": execution_settings.runtime_version,
    }
    if cleanup_cancel_all is not None:
        payload["cleanup_cancel_all"] = cleanup_cancel_all
    if cleanup_error:
        payload["cleanup_error"] = cleanup_error
    if live_risk is not None:
        payload["live_risk"] = live_risk
    return payload


def cancel_stale_orders(
    *,
    trader: Any,
    prior_order_timestamps: dict[str, str],
    stale_order_max_age_seconds: int = DEFAULT_STALE_ORDER_MAX_AGE_SECONDS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Cancel orders older than stale_order_max_age_seconds.

    Returns a summary of stale orders found and cancel results.
    """
    if not prior_order_timestamps:
        return {"stale_count": 0, "cancelled": []}

    now = datetime.now(tz=timezone.utc)
    stale_ids: list[str] = []
    for order_id, placed_at_str in prior_order_timestamps.items():
        try:
            placed_at = datetime.fromisoformat(placed_at_str)
            if (now - placed_at).total_seconds() > stale_order_max_age_seconds:
                stale_ids.append(order_id)
        except (ValueError, TypeError):
            stale_ids.append(order_id)

    if not stale_ids:
        return {"stale_count": 0, "cancelled": []}

    cancelled: list[dict[str, Any]] = []
    for order_id in stale_ids:
        try:
            if hasattr(trader, "cancel_order"):
                result = trader.cancel_order(order_id)
                cancelled.append({"order_id": order_id, "status": "cancelled", "response": result})
            else:
                cancelled.append({"order_id": order_id, "status": "skipped", "reason": "no_cancel_order_method"})
        except Exception as exc:
            cancelled.append({"order_id": order_id, "status": "error", "error": str(exc)})

    return {"stale_count": len(stale_ids), "cancelled": cancelled}


def execute_single_market_quotes(
    *,
    trader: PolymarketPublisherTrader | DirectClobTrader,
    quotes: list[dict[str, Any]],
    markets: list[dict[str, Any]],
    execution_settings: LiveExecutionSettings,
) -> dict[str, Any]:
    market_by_id = {
        safe_str(market.get("market_id"), ""): market
        for market in markets
        if isinstance(market, dict)
    }
    started_at = time.monotonic()
    placements: list[dict[str, Any]] = []
    skips: list[dict[str, Any]] = []
    cancel_response = None
    live_risk_state: dict[str, Any] | None = None
    try:
        _check_cycle_deadline(
            started_at=started_at,
            execution_settings=execution_settings,
            stage="initial_positions",
        )
        raw_positions = _invoke_trader_call(
            "get_positions_initial",
            trader.get_positions,
            execution_settings,
        )
        position_sizes = positions_by_key(raw_positions)
        if execution_settings.cancel_before_requote:
            cancel_response = _invoke_trader_call(
                "cancel_all_before_requote",
                trader.cancel_all,
                execution_settings,
            )

        live_risk = _capture_live_risk(
            trader=trader,
            exposure_by_key=single_market_inventory_notional(
                raw_positions=raw_positions,
                markets=markets,
            ),
            execution_settings=execution_settings,
        )
        live_risk_state = live_risk.get("state")
        if live_risk.get("status") != "ok":
            return _live_failure_payload(
                trader=trader,
                execution_settings=execution_settings,
                error_code=safe_str(live_risk.get("error_code"), "live_risk_guard_blocked"),
                message=safe_str(live_risk.get("message"), "Live risk guard blocked execution."),
                cancel_response=cancel_response,
                orders_submitted=placements,
                order_skips=skips,
                live_risk=live_risk_state,
            )

        available_cash_usd = max(
            0.0,
            safe_float((live_risk_state or {}).get("cash_balance_usd"), 0.0),
        )

        for quote in quotes:
            market_id = safe_str(quote.get("market_id"), "")
            _check_cycle_deadline(
                started_at=started_at,
                execution_settings=execution_settings,
                stage=f"quote:{market_id or 'unknown'}",
            )
            market = market_by_id.get(market_id)
            if not market:
                skips.append({"market_id": market_id, "reason": "missing_live_market"})
                continue
            token_id = safe_str(market.get("token_id"), safe_str(market.get("market_id"), ""))
            tick_size = safe_str(market.get("tick_size"), "0.01")
            neg_risk = bool(market.get("neg_risk", False))
            if neg_risk and hasattr(trader, "preflight_neg_risk"):
                nr_status = trader.preflight_neg_risk()
                if not nr_status.get("checks_passed", True):
                    skips.append({
                        "market_id": market["market_id"],
                        "reason": "neg_risk_approval_missing",
                        "errors": nr_status.get("errors", []),
                        "hint": (
                            f"Approve USDC.e and CT to NegRiskAdapter "
                            f"({POLYGON_NEG_RISK_ADAPTER}) on Polygon. "
                            f"See issue #159 for details."
                        ),
                    })
                    continue
            fee_rate_bps = fetch_fee_rate_bps(token_id)
            fallback_notional = max(0.0, safe_float(quote.get("quote_notional_usd"), 0.0))
            bid_notional = max(0.0, safe_float(quote.get("bid_notional_usd"), fallback_notional))
            ask_notional = max(0.0, safe_float(quote.get("ask_notional_usd"), fallback_notional))
            if bid_notional <= 0.0 and ask_notional <= 0.0:
                skips.append({"market_id": market["market_id"], "reason": "zero_quote_notional"})
                continue

            bid_price = snap_price(safe_float(quote.get("bid_price"), 0.0), tick_size, "BUY")
            ask_price = snap_price(safe_float(quote.get("ask_price"), 0.0), tick_size, "SELL")
            sell_only = bool(market.get("sell_only", False)) or bool(quote.get("sell_only", False))

            if bid_price > 0.0 and bid_notional > 0.0 and not sell_only:
                remaining_cash_usd = available_cash_usd - bid_notional
                if (
                    execution_settings.min_cash_reserve_usd > 0.0
                    and remaining_cash_usd < execution_settings.min_cash_reserve_usd
                ):
                    skips.append(
                        {
                            "market_id": market["market_id"],
                            "reason": "cash_reserve_guard",
                            "available_cash_usd": round(available_cash_usd, 4),
                            "requested_notional_usd": round(bid_notional, 4),
                            "min_cash_reserve_usd": round(execution_settings.min_cash_reserve_usd, 4),
                        }
                    )
                else:
                    bid_size = bid_notional / max(bid_price, 1e-9)
                    try:
                        response = _invoke_trader_call(
                            f"create_order_buy:{market['market_id']}",
                            lambda: trader.create_order(
                                token_id=token_id,
                                side="BUY",
                                price=bid_price,
                                size=bid_size,
                                tick_size=tick_size,
                                neg_risk=neg_risk,
                                fee_rate_bps=fee_rate_bps,
                            ),
                            execution_settings,
                        )
                        placements.append(
                            {
                                "market_id": market["market_id"],
                                "token_id": token_id,
                                "side": "BUY",
                                "price": bid_price,
                                "size": round(bid_size, 6),
                                "response": response,
                            }
                        )
                        available_cash_usd = max(0.0, remaining_cash_usd)
                    except Exception as order_exc:
                        skips.append(
                            {
                                "market_id": market["market_id"],
                                "reason": "order_placement_failed",
                                "side": "BUY",
                                "error": str(order_exc),
                            }
                        )

            available_shares = max(0.0, position_sizes.get(token_id, 0.0))
            sell_notional = min(ask_notional, available_shares * max(ask_price, 0.0))
            if ask_price > 0.0 and sell_notional > 0.0:
                ask_size = sell_notional / max(ask_price, 1e-9)
                try:
                    response = _invoke_trader_call(
                        f"create_order_sell:{market['market_id']}",
                        lambda: trader.create_order(
                            token_id=token_id,
                            side="SELL",
                            price=ask_price,
                            size=ask_size,
                            tick_size=tick_size,
                            neg_risk=neg_risk,
                            fee_rate_bps=fee_rate_bps,
                        ),
                        execution_settings,
                    )
                    placements.append(
                        {
                            "market_id": market["market_id"],
                            "token_id": token_id,
                            "side": "SELL",
                            "price": ask_price,
                            "size": round(ask_size, 6),
                            "response": response,
                        }
                    )
                except Exception as order_exc:
                    skips.append(
                        {
                            "market_id": market["market_id"],
                            "reason": "order_placement_failed",
                            "side": "SELL",
                            "error": str(order_exc),
                        }
                    )
            else:
                skips.append(
                    {
                        "market_id": market["market_id"],
                        "reason": "insufficient_inventory_for_sell",
                        "available_shares": round(available_shares, 6),
                    }
                )

        latest_orders: Any = []
        latest_positions: Any = raw_positions
        for poll_idx in range(execution_settings.poll_attempts):
            if execution_settings.poll_interval_seconds > 0:
                time.sleep(execution_settings.poll_interval_seconds)
            _check_cycle_deadline(
                started_at=started_at,
                execution_settings=execution_settings,
                stage=f"poll:{poll_idx + 1}",
            )
            latest_orders = _invoke_trader_call(
                "get_orders_poll",
                trader.get_orders,
                execution_settings,
            )
            latest_positions = _invoke_trader_call(
                "get_positions_poll",
                trader.get_positions,
                execution_settings,
            )

        updated_inventory = single_market_inventory_notional(
            raw_positions=latest_positions,
            markets=markets,
        )
        final_risk = _capture_live_risk(
            trader=trader,
            exposure_by_key=updated_inventory,
            execution_settings=execution_settings,
        )
        live_risk_state = final_risk.get("state")
        if final_risk.get("status") != "ok":
            return _live_failure_payload(
                trader=trader,
                execution_settings=execution_settings,
                error_code=safe_str(final_risk.get("error_code"), "live_risk_guard_blocked"),
                message=safe_str(final_risk.get("message"), "Live risk guard blocked execution."),
                cancel_response=cancel_response,
                orders_submitted=placements,
                order_skips=skips,
                live_risk=live_risk_state,
            )

        placed_at_iso = datetime.now(tz=timezone.utc).isoformat()
        order_timestamps = {
            oid: placed_at_iso for oid in active_order_ids(latest_orders)
        }
        return {
            "status": "ok",
            "cancel_all": cancel_response,
            "orders_submitted": placements,
            "order_skips": skips,
            "open_orders": latest_orders,
            "open_order_ids": active_order_ids(latest_orders),
            "order_timestamps": order_timestamps,
            "positions": latest_positions,
            "updated_inventory": updated_inventory,
            "live_risk": live_risk_state,
            "runtime_version": execution_settings.runtime_version,
        }
    except TimeoutError as exc:
        return _live_failure_payload(
            trader=trader,
            execution_settings=execution_settings,
            error_code="live_operation_timeout",
            message=str(exc),
            cancel_response=cancel_response,
            orders_submitted=placements,
            order_skips=skips,
            live_risk=live_risk_state,
        )
    except Exception as exc:
        return _live_failure_payload(
            trader=trader,
            execution_settings=execution_settings,
            error_code="live_operation_failed",
            message=str(exc),
            cancel_response=cancel_response,
            orders_submitted=placements,
            order_skips=skips,
            live_risk=live_risk_state,
        )


def execute_pair_trades(
    *,
    trader: PolymarketPublisherTrader | DirectClobTrader,
    pair_trades: list[dict[str, Any]],
    markets: list[dict[str, Any]],
    execution_settings: LiveExecutionSettings,
) -> dict[str, Any]:
    market_by_id = {
        safe_str(market.get("market_id"), ""): market
        for market in markets
        if isinstance(market, dict)
    }
    started_at = time.monotonic()
    placements: list[dict[str, Any]] = []
    skips: list[dict[str, Any]] = []
    cancel_response = None
    live_risk_state: dict[str, Any] | None = None
    try:
        _check_cycle_deadline(
            started_at=started_at,
            execution_settings=execution_settings,
            stage="initial_positions",
        )
        raw_positions = _invoke_trader_call(
            "get_positions_initial",
            trader.get_positions,
            execution_settings,
        )
        position_sizes = positions_by_key(raw_positions)
        if execution_settings.cancel_before_requote:
            cancel_response = _invoke_trader_call(
                "cancel_all_before_requote",
                trader.cancel_all,
                execution_settings,
            )

        live_risk = _capture_live_risk(
            trader=trader,
            exposure_by_key=pair_leg_exposure_notional(
                raw_positions=raw_positions,
                markets=markets,
            ),
            execution_settings=execution_settings,
        )
        live_risk_state = live_risk.get("state")
        if live_risk.get("status") != "ok":
            return _live_failure_payload(
                trader=trader,
                execution_settings=execution_settings,
                error_code=safe_str(live_risk.get("error_code"), "live_risk_guard_blocked"),
                message=safe_str(live_risk.get("message"), "Live risk guard blocked execution."),
                cancel_response=cancel_response,
                orders_submitted=placements,
                order_skips=skips,
                live_risk=live_risk_state,
            )

        available_cash_usd = max(
            0.0,
            safe_float((live_risk_state or {}).get("cash_balance_usd"), 0.0),
        )

        for trade in pair_trades:
            market_id = safe_str(trade.get("market_id"), "")
            _check_cycle_deadline(
                started_at=started_at,
                execution_settings=execution_settings,
                stage=f"pair:{market_id or 'unknown'}",
            )
            market = market_by_id.get(market_id)
            if not market:
                skips.append({"market_id": market_id, "reason": "missing_live_pair"})
                continue
            pair_market_id = safe_str(market.get("pair_market_id"), "")
            legs = trade.get("legs")
            if not isinstance(legs, list) or len(legs) != 2:
                skips.append({"market_id": market["market_id"], "reason": "invalid_pair_legs"})
                continue

            leg_specs: list[dict[str, Any]] = []
            skip_reason = ""
            buy_notional_usd = 0.0
            for leg in legs:
                if not isinstance(leg, dict):
                    skip_reason = "invalid_leg"
                    break
                leg_market_id = safe_str(leg.get("market_id"), "")
                side = safe_str(leg.get("side"), "").upper()
                if leg_market_id == safe_str(market.get("market_id"), ""):
                    token_id = safe_str(market.get("token_id"), leg_market_id)
                    price = safe_float(
                        market.get("best_bid") if side == "BUY" else market.get("best_ask"),
                        0.0,
                    )
                    tick_size = safe_str(market.get("tick_size"), "0.01")
                    neg_risk = bool(market.get("neg_risk", False))
                elif leg_market_id == pair_market_id:
                    token_id = safe_str(market.get("pair_token_id"), leg_market_id)
                    price = safe_float(
                        market.get("pair_best_bid") if side == "BUY" else market.get("pair_best_ask"),
                        0.0,
                    )
                    tick_size = safe_str(market.get("pair_tick_size"), "0.01")
                    neg_risk = bool(market.get("pair_neg_risk", False))
                else:
                    skip_reason = "unknown_leg_market"
                    break

                if price <= 0.0:
                    skip_reason = "invalid_leg_price"
                    break

                notional = max(0.0, safe_float(leg.get("notional_usd"), 0.0))
                size = notional / max(price, 1e-9)
                if side == "SELL":
                    available_shares = max(0.0, position_sizes.get(token_id, 0.0))
                    if available_shares + 1e-9 < size:
                        skip_reason = "insufficient_inventory_for_pair_sell"
                        break
                else:
                    buy_notional_usd += notional
                leg_specs.append(
                    {
                        "market_id": leg_market_id,
                        "token_id": token_id,
                        "side": side,
                        "price": snap_price(price, tick_size, side),
                        "size": size,
                        "tick_size": tick_size,
                        "neg_risk": neg_risk,
                        "notional_usd": round(notional, 4),
                    }
                )

            if not skip_reason and any(ls.get("neg_risk") for ls in leg_specs):
                if hasattr(trader, "preflight_neg_risk"):
                    nr_status = trader.preflight_neg_risk()
                    if not nr_status.get("checks_passed", True):
                        skip_reason = "neg_risk_approval_missing"

            if not skip_reason and (
                execution_settings.min_cash_reserve_usd > 0.0
                and (available_cash_usd - buy_notional_usd) < execution_settings.min_cash_reserve_usd
            ):
                skip_reason = "cash_reserve_guard"

            if skip_reason:
                skip_payload = {
                    "market_id": market["market_id"],
                    "pair_market_id": pair_market_id,
                    "reason": skip_reason,
                }
                if skip_reason == "cash_reserve_guard":
                    skip_payload["available_cash_usd"] = round(available_cash_usd, 4)
                    skip_payload["requested_buy_notional_usd"] = round(buy_notional_usd, 4)
                    skip_payload["min_cash_reserve_usd"] = round(execution_settings.min_cash_reserve_usd, 4)
                skips.append(skip_payload)
                continue

            for leg_spec in leg_specs:
                fee_rate_bps = fetch_fee_rate_bps(leg_spec["token_id"])
                response = _invoke_trader_call(
                    f"create_order_{leg_spec['side'].lower()}:{leg_spec['market_id']}",
                    lambda leg_spec=leg_spec, fee_rate_bps=fee_rate_bps: trader.create_order(
                        token_id=leg_spec["token_id"],
                        side=leg_spec["side"],
                        price=leg_spec["price"],
                        size=leg_spec["size"],
                        tick_size=leg_spec["tick_size"],
                        neg_risk=leg_spec["neg_risk"],
                        fee_rate_bps=fee_rate_bps,
                    ),
                    execution_settings,
                )
                placements.append({**leg_spec, "response": response})
            available_cash_usd = max(0.0, available_cash_usd - buy_notional_usd)

        latest_orders: Any = []
        latest_positions: Any = raw_positions
        for poll_idx in range(execution_settings.poll_attempts):
            if execution_settings.poll_interval_seconds > 0:
                time.sleep(execution_settings.poll_interval_seconds)
            _check_cycle_deadline(
                started_at=started_at,
                execution_settings=execution_settings,
                stage=f"poll:{poll_idx + 1}",
            )
            latest_orders = _invoke_trader_call(
                "get_orders_poll",
                trader.get_orders,
                execution_settings,
            )
            latest_positions = _invoke_trader_call(
                "get_positions_poll",
                trader.get_positions,
                execution_settings,
            )

        updated_leg_exposure = pair_leg_exposure_notional(
            raw_positions=latest_positions,
            markets=markets,
        )
        final_risk = _capture_live_risk(
            trader=trader,
            exposure_by_key=updated_leg_exposure,
            execution_settings=execution_settings,
        )
        live_risk_state = final_risk.get("state")
        if final_risk.get("status") != "ok":
            return _live_failure_payload(
                trader=trader,
                execution_settings=execution_settings,
                error_code=safe_str(final_risk.get("error_code"), "live_risk_guard_blocked"),
                message=safe_str(final_risk.get("message"), "Live risk guard blocked execution."),
                cancel_response=cancel_response,
                orders_submitted=placements,
                order_skips=skips,
                live_risk=live_risk_state,
            )

        placed_at_iso = datetime.now(tz=timezone.utc).isoformat()
        order_timestamps = {
            oid: placed_at_iso for oid in active_order_ids(latest_orders)
        }
        return {
            "status": "ok",
            "cancel_all": cancel_response,
            "orders_submitted": placements,
            "order_skips": skips,
            "open_orders": latest_orders,
            "open_order_ids": active_order_ids(latest_orders),
            "order_timestamps": order_timestamps,
            "positions": latest_positions,
            "updated_leg_exposure": updated_leg_exposure,
            "live_risk": live_risk_state,
            "runtime_version": execution_settings.runtime_version,
        }
    except TimeoutError as exc:
        return _live_failure_payload(
            trader=trader,
            execution_settings=execution_settings,
            error_code="live_operation_timeout",
            message=str(exc),
            cancel_response=cancel_response,
            orders_submitted=placements,
            order_skips=skips,
            live_risk=live_risk_state,
        )
    except Exception as exc:
        return _live_failure_payload(
            trader=trader,
            execution_settings=execution_settings,
            error_code="live_operation_failed",
            message=str(exc),
            cancel_response=cancel_response,
            orders_submitted=placements,
            order_skips=skips,
            live_risk=live_risk_state,
        )


def sell_held_inventory(
    *,
    trader: PolymarketPublisherTrader,
    raw_positions: Any,
    covered_token_ids: set[str],
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    """Sell any held tokens not already covered by the main execution pass.

    Pair bots may leave individual legs unsold because they only trade in
    pairs.  This function generates SELL-only orders to unwind those positions.
    """
    sizes = positions_by_key(raw_positions)
    sells: list[dict[str, Any]] = []
    for token_id, shares in sizes.items():
        if shares <= 0 or token_id in covered_token_ids:
            continue
        try:
            book = fetch_book(token_id, timeout_seconds=timeout_seconds)
            ask_price = safe_float(book.get("best_ask"), 0.0)
            tick_size = safe_str(book.get("tick_size"), "0.01")
            neg_risk = bool(book.get("neg_risk", False))
            if ask_price <= 0.0:
                continue
            ask_price = snap_price(ask_price, tick_size, "SELL")
            size = shares
            fee_rate_bps = fetch_fee_rate_bps(token_id, timeout_seconds=timeout_seconds)
            response = trader.create_order(
                token_id=token_id,
                side="SELL",
                price=ask_price,
                size=size,
                tick_size=tick_size,
                neg_risk=neg_risk,
                fee_rate_bps=fee_rate_bps,
            )
            sells.append(
                {
                    "token_id": token_id,
                    "side": "SELL",
                    "price": ask_price,
                    "size": round(size, 6),
                    "source": "held-inventory-unwind",
                    "response": response,
                }
            )
        except Exception:
            continue
    return sells
