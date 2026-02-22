#!/usr/bin/env python3
"""Curve Gauge Yield Trader runtime with paper-first live-trading guards."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
from datetime import datetime, timezone
from urllib.parse import urlencode
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_DRY_RUN = True
DEFAULT_API_BASE = "https://api.serendb.com"
DEFAULT_WALLET_PATH = "state/wallet.local.json"
DEFAULT_POSITION_SYNC_PATH = "/positions/update"
DEFAULT_RPC_PROBES = (
    {
        "method": "GET",
        "path": "/health",
        "body": {},
    },
    {
        "method": "POST",
        "path": "/",
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
        normalized_path = path if path.startswith("/") else f"/{path}"
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
            "Install with: pip install eth-account"
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
            "address": wallet["address"],
            "private_key_hex": wallet["private_key_hex"],
        }

    if not ledger_address:
        raise ConfigError(
            "ledger mode requires --ledger-address or config.wallet.ledger_address."
        )
    return {
        "mode": "ledger",
        "address": ledger_address,
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
        path = str(probe.get("path", ""))
        body = probe.get("body", {})
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            raise ConfigError(
                f"rpc_capability.probes[{index}].method '{method}' is not supported."
            )
        if not path.startswith("/"):
            raise ConfigError(f"rpc_capability.probes[{index}].path must start with '/'.")
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
            return {
                "status": "ok",
                "required": required,
                "connector": connector_alias,
                "publisher": publisher,
                "publisher_source": publisher_source,
                "probe": {"method": method, "path": path},
                "response_preview": sorted(response.keys()),
            }
        except PublisherError as exc:
            errors.append(f"{method} {path}: {exc}")

    probe_labels = ", ".join(f"{p['method']} {p['path']}" for p in probes)
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


def fetch_top_gauges(
    client: SerenPublisherClient,
    *,
    chain: str,
    limit: int,
) -> dict[str, Any]:
    return client.call(
        publisher="curve-finance",
        method="GET",
        path="/gauges/highest-rewards",
        body={"chain": chain, "limit": limit},
    )


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

    gauge_address = top.get("address") or "unknown"
    reward_apy = top.get("reward_apy") or top.get("apr") or "unknown"
    return {
        "token": token,
        "amount_usd": amount_usd,
        "gauge_address": gauge_address,
        "expected_reward_apy": reward_apy,
    }


def preflight_liquidity(
    client: SerenPublisherClient,
    *,
    chain: str,
    signer: dict[str, Any],
    trade_plan: dict[str, Any],
) -> dict[str, Any]:
    body = {
        "chain": chain,
        "action": "add_liquidity_to_curve_gauge",
        "signer_mode": signer["mode"],
        "signer_address": signer["address"],
        "trade_plan": trade_plan,
    }
    return client.call(
        publisher="evm-exec",
        method="POST",
        path="/preflight/liquidity",
        body=body,
    )


def sync_positions(
    client: SerenPublisherClient,
    *,
    chain: str,
    signer: dict[str, Any],
    sync_path: str,
) -> dict[str, Any]:
    body = {
        "chain": chain,
        "address": signer["address"],
    }
    return client.call(
        publisher="evm-exec",
        method="POST",
        path=sync_path,
        body=body,
    )


def execute_live_trade(
    client: SerenPublisherClient,
    *,
    chain: str,
    signer: dict[str, Any],
    trade_plan: dict[str, Any],
) -> dict[str, Any]:
    body = {
        "chain": chain,
        "action": "add_liquidity_to_curve_gauge",
        "signer_mode": signer["mode"],
        "signer_address": signer["address"],
        "trade_plan": trade_plan,
    }
    return client.call(
        publisher="evm-exec",
        method="POST",
        path="/trade/liquidity",
        body=body,
    )


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
    wallet_path = Path(str(config.get("wallet", {}).get("path", DEFAULT_WALLET_PATH)))
    ledger_from_config = str(config.get("wallet", {}).get("ledger_address", ""))
    resolved_ledger = ledger_address or ledger_from_config

    signer = resolve_signer(
        wallet_mode=inputs["wallet_mode"],
        wallet_path=wallet_path,
        ledger_address=resolved_ledger,
    )
    rpc_capability = check_rpc_capability(
        client,
        chain=inputs["chain"],
        config=config,
    )
    position_sync_config = config.get("position_sync", {})
    position_sync_enabled = True
    position_sync_path = DEFAULT_POSITION_SYNC_PATH
    if isinstance(position_sync_config, dict):
        position_sync_enabled = bool(position_sync_config.get("enabled", True))
        position_sync_path = str(
            position_sync_config.get("path", DEFAULT_POSITION_SYNC_PATH)
        )
    if not position_sync_path.startswith("/"):
        raise ConfigError("position_sync.path must start with '/'.")

    position_sync: dict[str, Any] = {"status": "skipped"}
    if position_sync_enabled:
        try:
            position_sync = sync_positions(
                client,
                chain=inputs["chain"],
                signer=signer,
                sync_path=position_sync_path,
            )
        except PublisherError as exc:
            if dry_run or not inputs["live_mode"]:
                position_sync = {"status": "warning", "error": str(exc)}
            else:
                raise ConfigError(
                    f"Position sync failed before live trade: {exc}"
                ) from exc
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
    preflight = preflight_liquidity(
        client,
        chain=inputs["chain"],
        signer=signer,
        trade_plan=trade_plan,
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
        chain=inputs["chain"],
        signer=signer,
        trade_plan=trade_plan,
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
