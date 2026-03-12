#!/usr/bin/env python3
"""Curve Gauge Yield Trader runtime with paper-first live-trading guards."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import sys

DEFAULT_DRY_RUN = True
DEFAULT_API_BASE = "https://api.serendb.com"
DEFAULT_WALLET_PATH = "state/wallet.local.json"
DEFAULT_GAS_LIMIT_MULTIPLIER = 1.2
DEFAULT_GAS_PRICE_MULTIPLIER = 1.1
DEFAULT_RPC_PROBES = (
    {
        "method": "POST",
        "path": "",
        "body": {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_chainId",
            "params": [],
        },
    },
    {
        "method": "POST",
        "path": "/ext/bc/C/rpc",
        "body": {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_chainId",
            "params": [],
        },
    },
    {
        "method": "POST",
        "path": "/rpc",
        "body": {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_chainId",
            "params": [],
        },
    },
    {
        "method": "GET",
        "path": "/health",
        "body": {},
    },
)
SUPPORTED_CHAINS = {
    "ethereum",
    "arbitrum",
    "base",
    "optimism",
    "polygon",
    "avalanche",
    "bsc",
    "gnosis",
    "zksync",
    "scroll",
}
CHAIN_DISCOVERY_TERMS: dict[str, tuple[str, ...]] = {
    "ethereum": ("ethereum",),
    "arbitrum": ("arbitrum",),
    "base": ("base",),
    "optimism": ("optimism",),
    "polygon": ("polygon", "matic"),
    "avalanche": ("avalanche", "avax"),
    "bsc": ("bsc", "binance", "bnb"),
    "gnosis": ("gnosis", "xdai"),
    "zksync": ("zksync",),
    "scroll": ("scroll",),
}
CURVE_CHAIN_ALIASES: dict[str, tuple[str, ...]] = {
    "ethereum": ("ethereum",),
    "arbitrum": ("arbitrum",),
    "base": ("base",),
    "optimism": ("optimism",),
    "polygon": ("polygon",),
    "avalanche": ("avalanche",),
    "bsc": ("bsc", "binance"),
    "gnosis": ("gnosis", "xdai"),
    "zksync": ("zksync",),
    "scroll": ("scroll",),
}
HEX_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
HEX_BYTES_RE = re.compile(r"^0x[a-fA-F0-9]*$")
MAX_UINT256 = (1 << 256) - 1


class ConfigError(Exception):
    pass


class PublisherError(Exception):
    pass


class SerenPublisherClient:
    def __init__(self, api_key: str, base_url: str = DEFAULT_API_BASE):
        self.api_key = api_key
        normalized = base_url.rstrip("/")
        for suffix in ("/v1/publishers", "/publishers"):
            if normalized.endswith(suffix):
                normalized = normalized[: -len(suffix)]
        self.base_url = normalized.rstrip("/")

    def _request(
        self,
        *,
        method: str,
        path: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        normalized_path = path if path.startswith("/") else f"/{path}"
        url = f"{self.base_url}{normalized_path}"
        method_upper = method.upper()
        data: bytes | None = None
        if method_upper != "GET":
            data = json.dumps(body or {}).encode("utf-8")
        request = Request(
            url=url,
            data=data,
            method=method_upper,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )

        try:
            with urlopen(request, timeout=30) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise PublisherError(f"HTTP {exc.code} on {normalized_path}: {details}") from exc
        except URLError as exc:
            raise PublisherError(f"Connection failed on {normalized_path}: {exc}") from exc

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise PublisherError(f"Invalid JSON from {normalized_path}: {raw[:200]}") from exc
        if not isinstance(parsed, dict):
            raise PublisherError(f"Response from {normalized_path} was not an object")
        return parsed

    def call(self, publisher: str, method: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        cleaned_path = path.strip()
        if cleaned_path in {"", "/"}:
            normalized_path = ""
        else:
            normalized_path = cleaned_path if cleaned_path.startswith("/") else f"/{cleaned_path}"

        try:
            return self._request(
                method=method,
                path=f"/publishers/{publisher}{normalized_path}",
                body=body,
            )
        except PublisherError as exc:
            raise PublisherError(f"{publisher} {exc}") from exc

    def list_publishers(self, *, limit: int = 100, max_pages: int = 5) -> list[dict[str, Any]]:
        publishers: list[dict[str, Any]] = []
        offset = 0

        for _ in range(max_pages):
            query = urlencode({"limit": limit, "offset": offset})
            payload = self._request(method="GET", path=f"/publishers?{query}", body={})
            data = payload.get("data")
            if not isinstance(data, list):
                raise PublisherError("Invalid publisher catalog response: missing data list.")

            page_items = [item for item in data if isinstance(item, dict)]
            publishers.extend(page_items)

            pagination = payload.get("pagination", {})
            has_more = bool(pagination.get("has_more")) if isinstance(pagination, dict) else False
            if not has_more or not page_items:
                break

            count = pagination.get("count") if isinstance(pagination, dict) else None
            if isinstance(count, int) and count > 0:
                offset += count
            else:
                offset += len(page_items)

        return publishers


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Curve Gauge Yield Trader runtime. Default mode is dry-run."
    )
    parser.add_argument("--config", default="config.json", help="Path to runtime config JSON.")
    parser.add_argument(
        "--init-wallet",
        action="store_true",
        help="Generate a local wallet file for live trading mode.",
    )
    parser.add_argument(
        "--wallet-path",
        default=DEFAULT_WALLET_PATH,
        help=f"Path for local wallet metadata (default: {DEFAULT_WALLET_PATH}).",
    )
    parser.add_argument(
        "--ledger-address",
        default="",
        help="Ledger EVM address to use when wallet_mode=ledger.",
    )
    parser.add_argument(
        "--yes-live",
        action="store_true",
        help="Required safety flag for live execution.",
    )
    return parser.parse_args()


def load_config(path: str) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(f"Config file not found: {config_path}")
    try:
        parsed = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON config: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ConfigError("Config must be a JSON object.")
    return parsed


def _resolve_inputs(config: dict[str, Any]) -> dict[str, Any]:
    inputs = config.get("inputs", {})
    if not isinstance(inputs, dict):
        raise ConfigError("Config field 'inputs' must be an object.")

    chain = str(inputs.get("chain", "ethereum"))
    wallet_mode = str(inputs.get("wallet_mode", "local"))
    live_mode = bool(inputs.get("live_mode", False))
    token = str(inputs.get("deposit_token", "USDC"))
    amount_usd = float(inputs.get("deposit_amount_usd", 100))
    top_n = int(inputs.get("top_n_gauges", 3))

    if chain not in SUPPORTED_CHAINS:
        raise ConfigError(f"Unsupported chain '{chain}'.")
    if wallet_mode not in {"local", "ledger"}:
        raise ConfigError("wallet_mode must be 'local' or 'ledger'.")
    if amount_usd <= 0:
        raise ConfigError("deposit_amount_usd must be > 0.")
    if top_n < 1:
        raise ConfigError("top_n_gauges must be >= 1.")

    return {
        "chain": chain,
        "wallet_mode": wallet_mode,
        "live_mode": live_mode,
        "deposit_token": token,
        "deposit_amount_usd": amount_usd,
        "top_n_gauges": top_n,
    }


def _extract_address_from_private_key(private_key_hex: str) -> str:
    try:
        from eth_account import Account  # type: ignore
    except Exception as exc:
        raise ConfigError(
            "eth-account is required for local wallet creation. "
            "Install with: pip install -r requirements.txt"
        ) from exc

    account = Account.from_key(private_key_hex)
    return str(account.address)


def create_local_wallet(wallet_path: Path) -> dict[str, Any]:
    private_key_hex = "0x" + secrets.token_hex(32)
    address = _extract_address_from_private_key(private_key_hex)
    wallet = {
        "mode": "local",
        "address": address,
        "private_key_hex": private_key_hex,
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    wallet_path.parent.mkdir(parents=True, exist_ok=True)
    wallet_path.write_text(json.dumps(wallet, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(wallet_path, 0o600)
    except PermissionError:
        pass
    return wallet


def load_local_wallet(wallet_path: Path) -> dict[str, Any]:
    if not wallet_path.exists():
        raise ConfigError(
            f"Local wallet file not found: {wallet_path}. "
            "Run with --init-wallet first."
        )
    wallet = json.loads(wallet_path.read_text(encoding="utf-8"))
    if not isinstance(wallet, dict):
        raise ConfigError("Local wallet file must contain a JSON object.")
    required = {"address", "private_key_hex"}
    if not required.issubset(set(wallet.keys())):
        raise ConfigError("Local wallet file is missing required fields.")
    return wallet


def resolve_signer(
    *,
    wallet_mode: str,
    wallet_path: Path,
    ledger_address: str,
) -> dict[str, Any]:
    if wallet_mode == "local":
        wallet = load_local_wallet(wallet_path)
        return {
            "mode": "local",
            "address": _normalize_address(str(wallet["address"]), "wallet.address"),
            "private_key_hex": str(wallet["private_key_hex"]),
        }

    if not ledger_address:
        raise ConfigError(
            "ledger mode requires --ledger-address or config.wallet.ledger_address."
        )
    return {
        "mode": "ledger",
        "address": _normalize_address(ledger_address, "ledger_address"),
    }


def _rpc_publisher_overrides(config: dict[str, Any]) -> dict[str, str]:
    overrides = config.get("rpc_publishers", {})
    if overrides is None:
        return {}
    if not isinstance(overrides, dict):
        raise ConfigError("Config field 'rpc_publishers' must be an object when provided.")

    cleaned: dict[str, str] = {}
    for chain, slug in overrides.items():
        if not isinstance(chain, str):
            raise ConfigError("rpc_publishers keys must be strings.")
        if chain not in SUPPORTED_CHAINS:
            raise ConfigError(f"rpc_publishers has unsupported chain key '{chain}'.")
        if not isinstance(slug, str) or not slug.strip():
            raise ConfigError(f"rpc_publishers['{chain}'] must be a non-empty string.")
        cleaned[chain] = slug.strip()
    return cleaned


def _is_rpc_like_publisher(publisher: dict[str, Any]) -> bool:
    categories = publisher.get("categories", [])
    categories_text = ""
    if isinstance(categories, list):
        categories_text = " ".join(
            str(category).lower() for category in categories if isinstance(category, str)
        )

    slug = str(publisher.get("slug", "")).lower()
    name = str(publisher.get("name", "")).lower()
    description = str(publisher.get("description", "")).lower()
    category_tokens = _tokenize(categories_text)
    slug_tokens = _tokenize(slug)
    name_tokens = _tokenize(name)

    if "rpc" in category_tokens or "rpc" in slug_tokens or "rpc" in name_tokens:
        return True
    return "json-rpc" in description or "json rpc" in description


def _tokenize(value: str) -> set[str]:
    return {token for token in re.split(r"[^a-z0-9]+", value.lower()) if token}


def _discovered_rpc_publishers(client: SerenPublisherClient) -> dict[str, str]:
    publishers = client.list_publishers()
    discovered: dict[str, str] = {}

    for chain, terms in CHAIN_DISCOVERY_TERMS.items():
        best_score = -1
        best_slug = ""
        for publisher in publishers:
            if not isinstance(publisher, dict):
                continue
            if publisher.get("is_active") is False:
                continue
            if not _is_rpc_like_publisher(publisher):
                continue

            slug = str(publisher.get("slug", "")).strip().lower()
            if not slug:
                continue
            name = str(publisher.get("name", "")).lower()
            description = str(publisher.get("description", "")).lower()
            categories = publisher.get("categories", [])
            categories_text = ""
            if isinstance(categories, list):
                categories_text = " ".join(
                    str(category).lower() for category in categories if isinstance(category, str)
                )

            slug_tokens = _tokenize(slug)
            name_tokens = _tokenize(name)
            category_tokens = _tokenize(categories_text)
            description_tokens = _tokenize(description)
            all_tokens = slug_tokens | name_tokens | category_tokens | description_tokens

            if not any(term in all_tokens for term in terms):
                continue

            score = 0
            if slug.startswith("seren-"):
                score += 20
            if any(term in slug_tokens for term in terms):
                score += 12
            if any(term in category_tokens for term in terms):
                score += 8
            if any(term in name_tokens for term in terms):
                score += 6
            if "json-rpc" in description:
                score += 4
            if score > best_score or (score == best_score and slug < best_slug):
                best_score = score
                best_slug = slug

        if best_slug:
            discovered[chain] = best_slug

    return discovered


def _rpc_publisher_for_chain(
    *,
    chain: str,
    client: SerenPublisherClient,
    config: dict[str, Any],
) -> tuple[str, str]:
    connector_alias = f"rpc_{chain}"
    overrides = _rpc_publisher_overrides(config)
    if chain in overrides:
        return overrides[chain], "config.rpc_publishers"

    discovered = _discovered_rpc_publishers(client)
    publisher = discovered.get(chain)
    if publisher:
        return publisher, "catalog:/publishers"

    available = ", ".join(
        f"{discovered_chain}:{slug}"
        for discovered_chain, slug in sorted(discovered.items())
    )
    available = available or "none"
    raise ConfigError(
        f"No RPC publisher is available for chain '{chain}' (connector alias '{connector_alias}'). "
        f"Auto-discovered mappings: {available}. "
        "Add an explicit override in config.rpc_publishers if needed."
    )


def _rpc_probe_config(config: dict[str, Any]) -> tuple[bool, list[dict[str, Any]]]:
    capability = config.get("rpc_capability", {})
    if not isinstance(capability, dict):
        raise ConfigError("Config field 'rpc_capability' must be an object when provided.")

    required = bool(capability.get("required", True))
    probes_raw = capability.get("probes")
    if probes_raw is None:
        return required, [dict(probe) for probe in DEFAULT_RPC_PROBES]

    if not isinstance(probes_raw, list) or not probes_raw:
        raise ConfigError("rpc_capability.probes must be a non-empty list.")

    probes: list[dict[str, Any]] = []
    for index, probe in enumerate(probes_raw):
        if not isinstance(probe, dict):
            raise ConfigError(f"rpc_capability.probes[{index}] must be an object.")
        method = str(probe.get("method", "GET")).upper()
        path = str(probe.get("path", "")).strip()
        body = probe.get("body", {})
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            raise ConfigError(
                f"rpc_capability.probes[{index}].method '{method}' is not supported."
            )
        if path and not path.startswith("/"):
            raise ConfigError(
                f"rpc_capability.probes[{index}].path must be '' or start with '/'."
            )
        if not isinstance(body, dict):
            raise ConfigError(f"rpc_capability.probes[{index}].body must be an object.")
        probes.append(
            {
                "method": method,
                "path": path,
                "body": body,
            }
        )
    return required, probes


def _path_label(path: str) -> str:
    return path or "(root)"


def _preview(value: Any) -> str:
    if isinstance(value, str):
        return value[:220]
    try:
        return json.dumps(value)[:220]
    except TypeError:
        return str(value)[:220]


def _unwrap_gateway_response(
    payload: dict[str, Any],
    *,
    publisher: str,
    method: str,
    path: str,
) -> Any:
    if not isinstance(payload, dict):
        raise PublisherError(
            f"{publisher} response for {method} {_path_label(path)} was not an object."
        )

    status = payload.get("status")
    if isinstance(status, int) and "body" in payload:
        body = payload.get("body")
        if status < 200 or status >= 300:
            raise PublisherError(
                f"{publisher} upstream {method} {_path_label(path)} returned status {status}: "
                f"{_preview(body)}"
            )
        return body
    return payload


def check_rpc_capability(
    client: SerenPublisherClient,
    *,
    chain: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    required, probes = _rpc_probe_config(config)
    connector_alias = f"rpc_{chain}"
    publisher, publisher_source = _rpc_publisher_for_chain(
        chain=chain,
        client=client,
        config=config,
    )
    errors: list[str] = []

    for probe in probes:
        method = str(probe["method"])
        path = str(probe["path"])
        body = dict(probe["body"])
        try:
            response = client.call(
                publisher=publisher,
                method=method,
                path=path,
                body=body,
            )
            unwrapped = _unwrap_gateway_response(
                response,
                publisher=publisher,
                method=method,
                path=path,
            )
            if method == "POST":
                if not isinstance(unwrapped, dict):
                    raise PublisherError(
                        f"{publisher} {method} {_path_label(path)} did not return a JSON object."
                    )
                if unwrapped.get("error") not in (None, {}):
                    raise PublisherError(
                        f"{publisher} {method} {_path_label(path)} returned JSON-RPC error: "
                        f"{_preview(unwrapped.get('error'))}"
                    )
                if "result" not in unwrapped:
                    raise PublisherError(
                        f"{publisher} {method} {_path_label(path)} missing JSON-RPC result."
                    )
                return {
                    "status": "ok",
                    "required": required,
                    "connector": connector_alias,
                    "publisher": publisher,
                    "publisher_source": publisher_source,
                    "probe": {"method": method, "path": path},
                    "rpc_target": {"method": method, "path": path},
                    "response_preview": sorted(unwrapped.keys()),
                }
        except PublisherError as exc:
            errors.append(f"{method} {_path_label(path)}: {exc}")

    probe_labels = ", ".join(f"{p['method']} {_path_label(str(p['path']))}" for p in probes)
    message = (
        f"RPC capability check failed for chain '{chain}' "
        f"(connector '{connector_alias}', publisher '{publisher}'). "
        f"Probes attempted: {probe_labels}."
    )
    if errors:
        message = f"{message} Errors: {' | '.join(errors)}"

    if required:
        raise ConfigError(message)
    return {
        "status": "warning",
        "required": required,
        "connector": connector_alias,
        "publisher": publisher,
        "publisher_source": publisher_source,
        "error": message,
    }


def _rpc_call(
    client: SerenPublisherClient,
    *,
    rpc_target: dict[str, Any],
    method_name: str,
    params: list[Any],
) -> tuple[Any, dict[str, Any]]:
    publisher = str(rpc_target["publisher"])
    method = str(rpc_target.get("method", "POST")).upper()
    path = str(rpc_target.get("path", ""))

    payload = client.call(
        publisher=publisher,
        method=method,
        path=path,
        body={
            "jsonrpc": "2.0",
            "id": 1,
            "method": method_name,
            "params": params,
        },
    )
    unwrapped = _unwrap_gateway_response(
        payload,
        publisher=publisher,
        method=method,
        path=path,
    )
    if not isinstance(unwrapped, dict):
        raise PublisherError(
            f"{publisher} {method} {_path_label(path)} returned non-object RPC payload."
        )
    if unwrapped.get("error") not in (None, {}):
        raise PublisherError(
            f"{publisher} RPC method {method_name} failed: {_preview(unwrapped.get('error'))}"
        )
    if "result" not in unwrapped:
        raise PublisherError(f"{publisher} RPC method {method_name} missing result.")
    return unwrapped["result"], unwrapped


def _parse_rpc_int(value: Any, *, field: str) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        try:
            if text.startswith(("0x", "0X")):
                return int(text, 16)
            return int(text)
        except ValueError as exc:
            raise PublisherError(f"RPC field '{field}' was not numeric: {value}") from exc
    raise PublisherError(f"RPC field '{field}' was not numeric: {value}")


def _parse_positive_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool):
        raise ConfigError(f"Config field '{field}' must be numeric, not bool.")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        try:
            if text.startswith(("0x", "0X")):
                parsed = int(text, 16)
            else:
                parsed = int(text)
        except ValueError as exc:
            raise ConfigError(f"Config field '{field}' must be numeric: {value}") from exc
    else:
        raise ConfigError(f"Config field '{field}' must be numeric.")
    if parsed <= 0:
        raise ConfigError(f"Config field '{field}' must be > 0.")
    return parsed


def _parse_nonnegative_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool):
        raise ConfigError(f"Config field '{field}' must be numeric, not bool.")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        try:
            if text.startswith(("0x", "0X")):
                parsed = int(text, 16)
            else:
                parsed = int(text)
        except ValueError as exc:
            raise ConfigError(f"Config field '{field}' must be numeric: {value}") from exc
    else:
        raise ConfigError(f"Config field '{field}' must be numeric.")
    if parsed < 0:
        raise ConfigError(f"Config field '{field}' must be >= 0.")
    return parsed


def _to_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def _normalize_address(value: str, field: str) -> str:
    text = str(value).strip()
    if not HEX_ADDRESS_RE.match(text):
        raise ConfigError(f"{field} must be a 0x-prefixed 20-byte address.")
    return text.lower()


def _normalize_hex_bytes(value: str, field: str) -> str:
    text = str(value).strip()
    if not HEX_BYTES_RE.match(text):
        raise ConfigError(f"{field} must be 0x-prefixed hex bytes.")
    return text.lower()


def _curve_chain_matches(requested_chain: str, source_chain: str) -> bool:
    aliases = CURVE_CHAIN_ALIASES.get(requested_chain, (requested_chain,))
    source = source_chain.lower().strip()
    return source in aliases


def _extract_reward_apy(gauge: dict[str, Any]) -> float:
    candidates: list[float] = []
    for field in ("gaugeFutureCrvApy", "gaugeCrvApy"):
        raw = gauge.get(field)
        if isinstance(raw, list):
            for item in raw:
                parsed = _to_float(item)
                if parsed is not None:
                    candidates.append(parsed)
        else:
            parsed = _to_float(raw)
            if parsed is not None:
                candidates.append(parsed)
    if not candidates:
        return 0.0
    return max(candidates)


def fetch_top_gauges(
    client: SerenPublisherClient,
    *,
    chain: str,
    limit: int,
) -> dict[str, Any]:
    payload = client.call(
        publisher="curve-finance",
        method="GET",
        path="/getGauges",
        body={},
    )
    body = _unwrap_gateway_response(
        payload,
        publisher="curve-finance",
        method="GET",
        path="/getGauges",
    )
    if not isinstance(body, dict):
        raise PublisherError("curve-finance /getGauges returned non-object body.")
    data = body.get("data")
    if not isinstance(data, dict):
        raise PublisherError("curve-finance /getGauges response missing data object.")

    gauges: list[dict[str, Any]] = []
    for gauge_name, gauge_value in data.items():
        if not isinstance(gauge_value, dict):
            continue
        source_chain = str(gauge_value.get("blockchainId", "")).lower().strip()
        if not _curve_chain_matches(chain, source_chain):
            continue

        gauge_address_raw = str(gauge_value.get("gauge", "")).strip()
        if not HEX_ADDRESS_RE.match(gauge_address_raw):
            continue

        swap_token_raw = str(gauge_value.get("swap_token", "")).strip()
        lp_token_address = swap_token_raw if HEX_ADDRESS_RE.match(swap_token_raw) else ""
        reward_apy = _extract_reward_apy(gauge_value)
        lp_token_price = _to_float(gauge_value.get("lpTokenPrice"))

        gauges.append(
            {
                "name": str(gauge_name),
                "address": gauge_address_raw.lower(),
                "pool_address": str(gauge_value.get("poolAddress", "")).strip().lower(),
                "lp_token_address": lp_token_address.lower(),
                "lp_token_price_usd": lp_token_price,
                "reward_apy": reward_apy,
                "source_chain": source_chain,
            }
        )

    gauges.sort(key=lambda item: float(item.get("reward_apy", 0.0)), reverse=True)
    top = gauges[:limit]
    if not top:
        raise ConfigError(
            f"Curve API returned no gauge candidates for chain '{chain}'. "
            "Verify chain support and publisher availability."
        )

    return {
        "gauges": top,
        "total_candidates": len(gauges),
        "source": "curve-finance:/getGauges",
    }


def choose_trade_plan(
    gauges_response: dict[str, Any],
    *,
    token: str,
    amount_usd: float,
) -> dict[str, Any]:
    gauges = gauges_response.get("gauges")
    if isinstance(gauges, list) and gauges:
        top = gauges[0] if isinstance(gauges[0], dict) else {}
    else:
        top = {}

    gauge_address = str(top.get("address") or "").strip()
    if not HEX_ADDRESS_RE.match(gauge_address):
        raise ConfigError("Unable to resolve a valid Curve gauge address from gauge data.")

    return {
        "token": token,
        "amount_usd": amount_usd,
        "gauge_address": gauge_address.lower(),
        "expected_reward_apy": _to_float(top.get("reward_apy")),
        "lp_token_address": str(top.get("lp_token_address", "")).strip().lower(),
        "lp_token_price_usd": _to_float(top.get("lp_token_price_usd")),
        "pool_address": str(top.get("pool_address", "")).strip().lower(),
        "gauge_name": str(top.get("name", "unknown")),
        "source": gauges_response.get("source", "curve-finance"),
    }


def _resolve_evm_execution(config: dict[str, Any]) -> dict[str, Any]:
    execution = config.get("evm_execution", {})
    if execution is None:
        execution = {}
    if not isinstance(execution, dict):
        raise ConfigError("Config field 'evm_execution' must be an object when provided.")

    strategy = str(execution.get("strategy", "gauge_stake_lp")).strip().lower()
    if strategy not in {"gauge_stake_lp", "custom_tx"}:
        raise ConfigError("evm_execution.strategy must be 'gauge_stake_lp' or 'custom_tx'.")
    execution["strategy"] = strategy
    return execution


def _encode_function_call(signature: str, arg_types: list[str], args: list[Any]) -> str:
    try:
        from eth_abi import encode as abi_encode  # type: ignore
        from eth_utils import keccak  # type: ignore
    except Exception as exc:
        raise ConfigError(
            "eth-abi and eth-utils are required for local EVM encoding. "
            "Install with: pip install -r requirements.txt"
        ) from exc

    selector = keccak(text=signature)[:4]
    encoded_args = abi_encode(arg_types, args)
    return "0x" + (selector + encoded_args).hex()


def _erc20_allowance(
    client: SerenPublisherClient,
    *,
    rpc_target: dict[str, Any],
    token_address: str,
    owner_address: str,
    spender_address: str,
) -> int:
    call_data = _encode_function_call(
        "allowance(address,address)",
        ["address", "address"],
        [owner_address, spender_address],
    )
    result, _ = _rpc_call(
        client,
        rpc_target=rpc_target,
        method_name="eth_call",
        params=[
            {
                "to": token_address,
                "data": call_data,
            },
            "latest",
        ],
    )
    return _parse_rpc_int(result, field="allowance")


def _erc20_balance_of(
    client: SerenPublisherClient,
    *,
    rpc_target: dict[str, Any],
    token_address: str,
    owner_address: str,
) -> int:
    call_data = _encode_function_call(
        "balanceOf(address)",
        ["address"],
        [owner_address],
    )
    result, _ = _rpc_call(
        client,
        rpc_target=rpc_target,
        method_name="eth_call",
        params=[
            {
                "to": token_address,
                "data": call_data,
            },
            "latest",
        ],
    )
    return _parse_rpc_int(result, field="balanceOf")


def _build_gauge_stake_lp_transactions(
    client: SerenPublisherClient,
    *,
    rpc_target: dict[str, Any],
    signer: dict[str, Any],
    trade_plan: dict[str, Any],
    execution: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    stake_cfg = execution.get("gauge_stake_lp", {})
    if stake_cfg is None:
        stake_cfg = {}
    if not isinstance(stake_cfg, dict):
        raise ConfigError("evm_execution.gauge_stake_lp must be an object when provided.")

    gauge_address = stake_cfg.get("gauge_address") or trade_plan.get("gauge_address")
    lp_token_address = stake_cfg.get("lp_token_address") or trade_plan.get("lp_token_address")
    gauge = _normalize_address(str(gauge_address), "gauge_stake_lp.gauge_address")
    lp_token = _normalize_address(str(lp_token_address), "gauge_stake_lp.lp_token_address")

    lp_amount_raw = stake_cfg.get("lp_amount_wei")
    if lp_amount_raw not in (None, ""):
        lp_amount_wei = _parse_positive_int(lp_amount_raw, field="gauge_stake_lp.lp_amount_wei")
    else:
        lp_price = _to_float(trade_plan.get("lp_token_price_usd"))
        amount_usd = _to_float(trade_plan.get("amount_usd"))
        decimals = int(stake_cfg.get("lp_token_decimals", 18))
        if decimals < 0 or decimals > 36:
            raise ConfigError("gauge_stake_lp.lp_token_decimals must be between 0 and 36.")
        if lp_price is None or lp_price <= 0:
            raise ConfigError(
                "Unable to derive LP amount from USD because lp_token_price_usd is missing/invalid. "
                "Set evm_execution.gauge_stake_lp.lp_amount_wei explicitly."
            )
        if amount_usd is None or amount_usd <= 0:
            raise ConfigError("Trade plan amount_usd is invalid.")
        token_amount = amount_usd / lp_price
        lp_amount_wei = int(token_amount * (10**decimals))
        if lp_amount_wei <= 0:
            raise ConfigError(
                "Derived LP amount is zero. Increase deposit_amount_usd or set lp_amount_wei."
            )

    approve_first = bool(stake_cfg.get("approve_first", True))
    approve_max = bool(stake_cfg.get("approve_max", True))

    tx_calls: list[dict[str, Any]] = []
    allowance_wei = _erc20_allowance(
        client,
        rpc_target=rpc_target,
        token_address=lp_token,
        owner_address=signer["address"],
        spender_address=gauge,
    )
    if approve_first and allowance_wei < lp_amount_wei:
        approve_amount = MAX_UINT256 if approve_max else lp_amount_wei
        approve_data = _encode_function_call(
            "approve(address,uint256)",
            ["address", "uint256"],
            [gauge, approve_amount],
        )
        tx_calls.append(
            {
                "label": "approve_lp_token",
                "to": lp_token,
                "value_wei": 0,
                "data": approve_data,
            }
        )

    deposit_data = _encode_function_call(
        "deposit(uint256)",
        ["uint256"],
        [lp_amount_wei],
    )
    tx_calls.append(
        {
            "label": "deposit_to_gauge",
            "to": gauge,
            "value_wei": 0,
            "data": deposit_data,
        }
    )

    details = {
        "strategy": "gauge_stake_lp",
        "gauge_address": gauge,
        "lp_token_address": lp_token,
        "lp_amount_wei": str(lp_amount_wei),
        "allowance_wei": str(allowance_wei),
        "approval_required": allowance_wei < lp_amount_wei,
    }
    return tx_calls, details


def _build_custom_tx_transactions(execution: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    custom = execution.get("custom_tx", {})
    if custom is None:
        custom = {}
    if not isinstance(custom, dict):
        raise ConfigError("evm_execution.custom_tx must be an object when provided.")

    to = _normalize_address(str(custom.get("to", "")), "custom_tx.to")
    data = _normalize_hex_bytes(str(custom.get("data", "0x")), "custom_tx.data")
    value_wei = _parse_nonnegative_int(custom.get("value_wei", 0), field="custom_tx.value_wei")
    label = str(custom.get("label", "custom_transaction")).strip() or "custom_transaction"

    tx_calls = [
        {
            "label": label,
            "to": to,
            "value_wei": value_wei,
            "data": data,
        }
    ]
    details = {
        "strategy": "custom_tx",
        "to": to,
        "value_wei": str(value_wei),
        "data_preview": f"{data[:18]}...{data[-8:]}" if len(data) > 28 else data,
    }
    return tx_calls, details


def _build_trade_transactions(
    client: SerenPublisherClient,
    *,
    rpc_target: dict[str, Any],
    signer: dict[str, Any],
    trade_plan: dict[str, Any],
    execution: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    strategy = str(execution.get("strategy", "gauge_stake_lp"))
    if strategy == "custom_tx":
        return _build_custom_tx_transactions(execution)
    return _build_gauge_stake_lp_transactions(
        client,
        rpc_target=rpc_target,
        signer=signer,
        trade_plan=trade_plan,
        execution=execution,
    )


def _resolve_gas_price_wei(
    client: SerenPublisherClient,
    *,
    rpc_target: dict[str, Any],
    execution: dict[str, Any],
) -> int:
    tx_cfg = execution.get("tx", {})
    if tx_cfg is None:
        tx_cfg = {}
    if not isinstance(tx_cfg, dict):
        raise ConfigError("evm_execution.tx must be an object when provided.")

    configured = tx_cfg.get("gas_price_wei")
    if configured not in (None, ""):
        return _parse_positive_int(configured, field="tx.gas_price_wei")

    base_gas_price_result, _ = _rpc_call(
        client,
        rpc_target=rpc_target,
        method_name="eth_gasPrice",
        params=[],
    )
    base_gas_price = _parse_rpc_int(base_gas_price_result, field="eth_gasPrice")
    multiplier = _to_float(tx_cfg.get("gas_price_multiplier"))
    if multiplier is None:
        multiplier = DEFAULT_GAS_PRICE_MULTIPLIER
    if multiplier <= 0:
        raise ConfigError("tx.gas_price_multiplier must be > 0.")
    return max(1, int(math.ceil(base_gas_price * multiplier)))


def _resolve_gas_limit_multiplier(execution: dict[str, Any]) -> float:
    tx_cfg = execution.get("tx", {})
    if tx_cfg is None:
        tx_cfg = {}
    if not isinstance(tx_cfg, dict):
        raise ConfigError("evm_execution.tx must be an object when provided.")

    multiplier = _to_float(tx_cfg.get("gas_limit_multiplier"))
    if multiplier is None:
        multiplier = DEFAULT_GAS_LIMIT_MULTIPLIER
    if multiplier <= 0:
        raise ConfigError("tx.gas_limit_multiplier must be > 0.")
    return multiplier


def _resolve_fallback_gas_limit(execution: dict[str, Any]) -> int:
    tx_cfg = execution.get("tx", {})
    if tx_cfg is None:
        tx_cfg = {}
    if not isinstance(tx_cfg, dict):
        raise ConfigError("evm_execution.tx must be an object when provided.")
    fallback = tx_cfg.get("fallback_gas_limit", 350_000)
    return _parse_positive_int(fallback, field="tx.fallback_gas_limit")


def _estimate_and_prepare_transactions(
    client: SerenPublisherClient,
    *,
    rpc_target: dict[str, Any],
    signer: dict[str, Any],
    tx_calls: list[dict[str, Any]],
    gas_price_wei: int,
    execution: dict[str, Any],
    strict_estimation: bool,
) -> dict[str, Any]:
    chain_id_result, _ = _rpc_call(
        client,
        rpc_target=rpc_target,
        method_name="eth_chainId",
        params=[],
    )
    nonce_result, _ = _rpc_call(
        client,
        rpc_target=rpc_target,
        method_name="eth_getTransactionCount",
        params=[signer["address"], "pending"],
    )
    chain_id = _parse_rpc_int(chain_id_result, field="eth_chainId")
    next_nonce = _parse_rpc_int(nonce_result, field="eth_getTransactionCount")

    gas_limit_multiplier = _resolve_gas_limit_multiplier(execution)
    fallback_gas_limit = _resolve_fallback_gas_limit(execution)

    prepared: list[dict[str, Any]] = []
    estimation_errors: list[str] = []
    for tx_call in tx_calls:
        label = str(tx_call["label"])
        to = str(tx_call["to"])
        value_wei = int(tx_call["value_wei"])
        data = str(tx_call["data"])

        estimate_payload = {
            "from": signer["address"],
            "to": to,
            "value": hex(value_wei),
            "data": data,
        }

        estimate_error = ""
        try:
            estimate_result, _ = _rpc_call(
                client,
                rpc_target=rpc_target,
                method_name="eth_estimateGas",
                params=[estimate_payload],
            )
            estimated_gas = _parse_rpc_int(estimate_result, field=f"eth_estimateGas:{label}")
        except PublisherError as exc:
            if strict_estimation:
                raise ConfigError(
                    f"Gas estimation failed for '{label}' in live mode: {exc}"
                ) from exc
            estimated_gas = fallback_gas_limit
            estimate_error = str(exc)
            estimation_errors.append(f"{label}: {exc}")

        gas_limit = max(21_000, int(math.ceil(estimated_gas * gas_limit_multiplier)))
        unsigned_tx = {
            "chainId": chain_id,
            "nonce": next_nonce,
            "to": to,
            "value": value_wei,
            "data": data,
            "gas": gas_limit,
            "gasPrice": gas_price_wei,
        }
        prepared.append(
            {
                "label": label,
                "to": to,
                "value_wei": str(value_wei),
                "estimated_gas": estimated_gas,
                "gas_limit": gas_limit,
                "estimate_error": estimate_error,
                "unsigned_tx": unsigned_tx,
            }
        )
        next_nonce += 1

    total_gas_limit = sum(int(item["gas_limit"]) for item in prepared)
    total_value_wei = sum(int(item["unsigned_tx"]["value"]) for item in prepared)
    estimated_network_fee_wei = total_gas_limit * gas_price_wei

    return {
        "chain_id": chain_id,
        "nonce_start": _parse_rpc_int(nonce_result, field="eth_getTransactionCount"),
        "gas_price_wei": gas_price_wei,
        "total_gas_limit": total_gas_limit,
        "total_value_wei": total_value_wei,
        "estimated_network_fee_wei": estimated_network_fee_wei,
        "estimation_errors": estimation_errors,
        "transactions": prepared,
    }


def preflight_liquidity(
    client: SerenPublisherClient,
    *,
    chain: str,
    signer: dict[str, Any],
    trade_plan: dict[str, Any],
    rpc_target: dict[str, Any],
    execution: dict[str, Any],
    strict_estimation: bool,
) -> dict[str, Any]:
    tx_calls, strategy_details = _build_trade_transactions(
        client,
        rpc_target=rpc_target,
        signer=signer,
        trade_plan=trade_plan,
        execution=execution,
    )
    gas_price_wei = _resolve_gas_price_wei(
        client,
        rpc_target=rpc_target,
        execution=execution,
    )
    prepared = _estimate_and_prepare_transactions(
        client,
        rpc_target=rpc_target,
        signer=signer,
        tx_calls=tx_calls,
        gas_price_wei=gas_price_wei,
        execution=execution,
        strict_estimation=strict_estimation,
    )

    return {
        "status": "ok",
        "execution_mode": "local_rpc",
        "chain": chain,
        "signer_mode": signer["mode"],
        "signer_address": signer["address"],
        "rpc_publisher": rpc_target["publisher"],
        "rpc_path": _path_label(str(rpc_target.get("path", ""))),
        "strategy": strategy_details,
        "chain_id": prepared["chain_id"],
        "nonce_start": prepared["nonce_start"],
        "gas_price_wei": str(prepared["gas_price_wei"]),
        "estimated_network_fee_wei": str(prepared["estimated_network_fee_wei"]),
        "total_value_wei": str(prepared["total_value_wei"]),
        "estimation_errors": prepared["estimation_errors"],
        "transactions": prepared["transactions"],
    }


def sync_positions(
    client: SerenPublisherClient,
    *,
    signer: dict[str, Any],
    rpc_target: dict[str, Any],
    trade_plan: dict[str, Any],
) -> dict[str, Any]:
    native_result, _ = _rpc_call(
        client,
        rpc_target=rpc_target,
        method_name="eth_getBalance",
        params=[signer["address"], "latest"],
    )
    native_balance_wei = _parse_rpc_int(native_result, field="eth_getBalance")

    positions: dict[str, Any] = {
        "status": "ok",
        "address": signer["address"],
        "native_balance_wei": str(native_balance_wei),
    }

    lp_token = str(trade_plan.get("lp_token_address", "")).strip()
    if HEX_ADDRESS_RE.match(lp_token):
        lp_balance_wei = _erc20_balance_of(
            client,
            rpc_target=rpc_target,
            token_address=lp_token.lower(),
            owner_address=signer["address"],
        )
        positions["lp_token_address"] = lp_token.lower()
        positions["lp_balance_wei"] = str(lp_balance_wei)

    gauge = str(trade_plan.get("gauge_address", "")).strip()
    if HEX_ADDRESS_RE.match(gauge):
        staked_balance_wei = _erc20_balance_of(
            client,
            rpc_target=rpc_target,
            token_address=gauge.lower(),
            owner_address=signer["address"],
        )
        positions["gauge_address"] = gauge.lower()
        positions["staked_balance_wei"] = str(staked_balance_wei)

    return positions


def _sign_transaction(unsigned_tx: dict[str, Any], private_key_hex: str) -> str:
    try:
        from eth_account import Account  # type: ignore
    except Exception as exc:
        raise ConfigError(
            "eth-account is required for local transaction signing. "
            "Install with: pip install -r requirements.txt"
        ) from exc

    signed = Account.sign_transaction(unsigned_tx, private_key_hex)
    raw_tx = getattr(signed, "raw_transaction", None)
    if raw_tx is None:
        raw_tx = getattr(signed, "rawTransaction", None)
    if raw_tx is None:
        raise ConfigError("Failed to extract signed raw transaction bytes.")
    return "0x" + bytes(raw_tx).hex()


def execute_live_trade(
    client: SerenPublisherClient,
    *,
    signer: dict[str, Any],
    preflight: dict[str, Any],
    rpc_target: dict[str, Any],
    execution: dict[str, Any],
) -> dict[str, Any]:
    transactions = preflight.get("transactions")
    if not isinstance(transactions, list) or not transactions:
        raise ConfigError("Preflight did not produce executable transactions.")

    tx_payloads: list[dict[str, Any]] = []
    for index, tx in enumerate(transactions):
        if not isinstance(tx, dict):
            raise ConfigError(f"preflight.transactions[{index}] must be an object.")
        unsigned_tx = tx.get("unsigned_tx")
        if not isinstance(unsigned_tx, dict):
            raise ConfigError(f"preflight.transactions[{index}].unsigned_tx is missing.")
        tx_payloads.append(unsigned_tx)

    submitted_hashes: list[str] = []
    if signer["mode"] == "local":
        private_key_hex = str(signer.get("private_key_hex", "")).strip()
        if not private_key_hex:
            raise ConfigError("Local signer requires private_key_hex.")

        for unsigned_tx in tx_payloads:
            raw_tx_hex = _sign_transaction(unsigned_tx, private_key_hex)
            tx_hash_result, _ = _rpc_call(
                client,
                rpc_target=rpc_target,
                method_name="eth_sendRawTransaction",
                params=[raw_tx_hex],
            )
            submitted_hashes.append(str(tx_hash_result))
        return {
            "status": "ok",
            "mode": "local_sign_and_submit",
            "submitted_tx_hashes": submitted_hashes,
        }

    ledger_cfg = execution.get("ledger", {})
    if ledger_cfg is None:
        ledger_cfg = {}
    if not isinstance(ledger_cfg, dict):
        raise ConfigError("evm_execution.ledger must be an object when provided.")
    signed_raw = ledger_cfg.get("signed_raw_transactions", [])
    if not isinstance(signed_raw, list):
        raise ConfigError("evm_execution.ledger.signed_raw_transactions must be an array.")
    if len(signed_raw) != len(tx_payloads):
        raise ConfigError(
            "Ledger live mode requires evm_execution.ledger.signed_raw_transactions with one raw "
            "transaction per preflight transaction."
        )

    for index, raw in enumerate(signed_raw):
        raw_hex = _normalize_hex_bytes(str(raw), f"ledger.signed_raw_transactions[{index}]")
        tx_hash_result, _ = _rpc_call(
            client,
            rpc_target=rpc_target,
            method_name="eth_sendRawTransaction",
            params=[raw_hex],
        )
        submitted_hashes.append(str(tx_hash_result))

    return {
        "status": "ok",
        "mode": "ledger_external_sign_and_submit",
        "submitted_tx_hashes": submitted_hashes,
    }


def run_once(config: dict[str, Any], *, yes_live: bool, ledger_address: str) -> dict[str, Any]:
    api_key = os.environ.get("SEREN_API_KEY", "").strip()
    if not api_key:
        raise ConfigError("SEREN_API_KEY is required in the environment.")

    config_api = config.get("api", {})
    configured_base_url = ""
    if isinstance(config_api, dict):
        configured_base_url = str(config_api.get("base_url", ""))
    base_url = configured_base_url or os.environ.get("SEREN_API_BASE_URL", DEFAULT_API_BASE)
    client = SerenPublisherClient(api_key=api_key, base_url=base_url)

    inputs = _resolve_inputs(config)
    dry_run = bool(config.get("dry_run", DEFAULT_DRY_RUN))
    wallet_config = config.get("wallet", {})
    if wallet_config is None:
        wallet_config = {}
    if not isinstance(wallet_config, dict):
        raise ConfigError("Config field 'wallet' must be an object when provided.")
    wallet_path = Path(str(wallet_config.get("path", DEFAULT_WALLET_PATH)))
    ledger_from_config = str(wallet_config.get("ledger_address", ""))
    resolved_ledger = ledger_address or ledger_from_config

    signer = resolve_signer(
        wallet_mode=inputs["wallet_mode"],
        wallet_path=wallet_path,
        ledger_address=resolved_ledger,
    )
    execution = _resolve_evm_execution(config)
    rpc_capability = check_rpc_capability(
        client,
        chain=inputs["chain"],
        config=config,
    )
    rpc_target = {
        "publisher": rpc_capability["publisher"],
        "method": str(rpc_capability.get("rpc_target", {}).get("method", "POST")),
        "path": str(rpc_capability.get("rpc_target", {}).get("path", "")),
        "publisher_source": rpc_capability.get("publisher_source", "unknown"),
    }

    gauges_response = fetch_top_gauges(
        client,
        chain=inputs["chain"],
        limit=inputs["top_n_gauges"],
    )
    trade_plan = choose_trade_plan(
        gauges_response,
        token=inputs["deposit_token"],
        amount_usd=inputs["deposit_amount_usd"],
    )

    position_sync_enabled = True
    position_sync_config = config.get("position_sync", {})
    if isinstance(position_sync_config, dict):
        position_sync_enabled = bool(position_sync_config.get("enabled", True))

    position_sync: dict[str, Any] = {"status": "skipped"}
    if position_sync_enabled:
        try:
            position_sync = sync_positions(
                client,
                signer=signer,
                rpc_target=rpc_target,
                trade_plan=trade_plan,
            )
        except PublisherError as exc:
            if dry_run or not inputs["live_mode"]:
                position_sync = {"status": "warning", "error": str(exc)}
            else:
                raise ConfigError(f"Position sync failed before live trade: {exc}") from exc

    preflight = preflight_liquidity(
        client,
        chain=inputs["chain"],
        signer=signer,
        trade_plan=trade_plan,
        rpc_target=rpc_target,
        execution=execution,
        strict_estimation=bool(inputs["live_mode"] and not dry_run and yes_live),
    )

    if dry_run or not inputs["live_mode"]:
        return {
            "status": "ok",
            "mode": "dry-run",
            "warning": (
                "No live transaction submitted. Set inputs.live_mode=true and pass --yes-live "
                "only after wallet funding and signer checks."
            ),
            "chain": inputs["chain"],
            "signer_mode": signer["mode"],
            "signer_address": signer["address"],
            "rpc_capability": rpc_capability,
            "position_sync": position_sync,
            "trade_plan": trade_plan,
            "preflight": preflight,
        }

    if not yes_live:
        raise ConfigError(
            "Live mode requested but --yes-live was not provided. "
            "Dry-run is the safe default."
        )

    live_execution = execute_live_trade(
        client,
        signer=signer,
        preflight=preflight,
        rpc_target=rpc_target,
        execution=execution,
    )
    return {
        "status": "ok",
        "mode": "live",
        "chain": inputs["chain"],
        "signer_mode": signer["mode"],
        "signer_address": signer["address"],
        "rpc_capability": rpc_capability,
        "position_sync": position_sync,
        "trade_plan": trade_plan,
        "preflight": preflight,
        "live_execution": live_execution,
    }



# ---------------------------------------------------------------------------
# SerenBucks balance helpers
# ---------------------------------------------------------------------------



def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _runtime_api_key() -> str:
    """Return the Seren API key from environment (Desktop or .env)."""
    for env_name in ("API_KEY", "SEREN_API_KEY"):
        token = (os.getenv(env_name) or "").strip()
        if token:
            return token
    return ""


def _check_serenbucks_balance(api_key: str) -> float:
    """Check SerenBucks balance. Returns balance in USD or 0.0 on error."""
    try:
        request = Request(
            "https://api.serendb.com/wallet/balance",
            headers={
                "User-Agent": "seren-curve-gauge-yield-trader/1.0",
                "Accept": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        with urlopen(request, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
            sb = data.get("serenbucks") or {}
            raw = sb.get("balance_usd") or sb.get("funded_balance_usd") or "0"
            return _safe_float(str(raw).replace("$", "").replace(",", ""), 0.0)
    except Exception as exc:
        print(f"WARNING: could not fetch SerenBucks balance: {exc}", file=sys.stderr)
        return 0.0

def main() -> int:
    args = parse_args()
    wallet_path = Path(args.wallet_path)
    if args.init_wallet:
        try:
            wallet = create_local_wallet(wallet_path)
        except ConfigError as exc:
            print(json.dumps({"status": "error", "error": str(exc)}))
            return 1
        print(
            json.dumps(
                {
                    "status": "ok",
                    "message": (
                        "Local wallet generated. Fund this wallet before live trading and keep "
                        "private key secure."
                    ),
                    "wallet_path": wallet_path.as_posix(),
                    "address": wallet["address"],
                }
            )
        )
        return 0

    try:
        config = load_config(args.config)
        result = run_once(
            config=config,
            yes_live=bool(args.yes_live),
            ledger_address=args.ledger_address.strip(),
        )
    except (ConfigError, PublisherError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 1

    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
