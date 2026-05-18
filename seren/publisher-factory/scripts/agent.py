#!/usr/bin/env python3
"""Generic SkillForge-style runtime for publisher-factory."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


DEFAULT_DRY_RUN = True
AVAILABLE_CONNECTORS = [
    "asana_template",
    "oauth_providers",
    "publisher_catalog",
    "research",
]
DEFAULT_GATEWAY_URL = "https://api.serendb.com"
DESTRUCTIVE_TERMS = (
    "archive",
    "cancel",
    "delete",
    "disable",
    "purge",
    "remove",
    "revoke",
)


class FactoryError(Exception):
    """Raised when a publisher-factory runtime dependency fails."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the generic Publisher Factory probe.")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--target", help="Company, product, or category to evaluate.")
    parser.add_argument("--gateway-url", default=DEFAULT_GATEWAY_URL)
    parser.add_argument("--deployment-mode", choices=("dry-run", "deploy"), default=None)
    parser.add_argument("--update-existing", action="store_true")
    parser.add_argument("--catalog-fixture")
    parser.add_argument("--asana-fixture")
    parser.add_argument("--organizations-fixture")
    parser.add_argument("--oauth-providers-fixture")
    return parser.parse_args()


def load_json_file(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise FactoryError(f"Expected JSON object in {path}")
    return payload


def load_config(config_path: str) -> dict[str, Any]:
    path = Path(config_path)
    if not path.exists():
        return {}
    return load_json_file(path)


def runtime_api_key() -> str:
    for name in ("API_KEY", "SEREN_API_KEY"):
        value = os.getenv(name, "").strip()
        if value:
            return value
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return ""
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if key.strip() in {"API_KEY", "SEREN_API_KEY"} and value.strip():
            return value.strip()
    return ""


def normalize_slug(value: str) -> str:
    lowered = value.strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", "-", lowered)
    return normalized.strip("-")


def unwrapped(payload: dict[str, Any]) -> Any:
    data = payload.get("data")
    if isinstance(data, dict) and "body" in data:
        return data["body"]
    if data is not None:
        return data
    return payload


def publisher_list(payload: dict[str, Any], key: str = "publishers") -> list[dict[str, Any]]:
    body = unwrapped(payload)
    if isinstance(body, dict):
        value = body.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        data = body.get("data")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
    if isinstance(body, list):
        return [item for item in body if isinstance(item, dict)]
    return []


class GatewayClient:
    def __init__(self, gateway_url: str, api_key: str) -> None:
        self.gateway_url = gateway_url.rstrip("/")
        self.api_key = api_key

    def request_json(self, path: str) -> dict[str, Any]:
        headers = {"Accept": "application/json", "User-Agent": "publisher-factory/1.0"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = Request(f"{self.gateway_url}{path}", headers=headers, method="GET")
        try:
            with urlopen(request, timeout=30) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            if exc.code == 404:
                return {"_not_found": True}
            detail = exc.read().decode("utf-8", errors="replace")
            raise FactoryError(f"GET {path} failed HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise FactoryError(f"GET {path} failed: {exc}") from exc
        return json.loads(raw)

    def list_publishers(self) -> list[dict[str, Any]]:
        publishers: list[dict[str, Any]] = []
        offset = 0
        while True:
            query = urlencode({"limit": 100, "offset": offset})
            payload = self.request_json(f"/publishers?{query}")
            page = publisher_list(payload)
            publishers.extend(page)
            pagination = payload.get("pagination")
            if not isinstance(pagination, dict) or not pagination.get("has_more") or not page:
                break
            offset += int(pagination.get("count") or len(page))
        return publishers

    def search_publishers(self, target: str) -> list[dict[str, Any]]:
        query = urlencode({"search": target, "limit": 50})
        return publisher_list(self.request_json(f"/publishers?{query}"))

    def get_publisher(self, slug: str) -> dict[str, Any] | None:
        payload = self.request_json(f"/publishers/{quote(slug)}")
        if payload.get("_not_found"):
            return None
        body = unwrapped(payload)
        return body if isinstance(body, dict) else None

    def list_organizations(self) -> list[dict[str, Any]]:
        body = unwrapped(self.request_json("/organizations"))
        if isinstance(body, dict):
            data = body.get("data")
            if isinstance(data, list):
                return [item for item in data if isinstance(item, dict)]
        if isinstance(body, list):
            return [item for item in body if isinstance(item, dict)]
        return []

    def list_oauth_providers(self, organization_id: str) -> list[dict[str, Any]]:
        paths = [
            f"/organizations/{quote(organization_id)}/oauth-providers",
            f"/organizations/{quote(organization_id)}/oauth/providers",
            f"/organizations/{quote(organization_id)}/oauth_providers",
        ]
        last_error: FactoryError | None = None
        for path in paths:
            try:
                payload = self.request_json(path)
            except FactoryError as exc:
                last_error = exc
                continue
            if payload.get("_not_found"):
                continue
            body = unwrapped(payload)
            if isinstance(body, dict):
                data = body.get("data")
                if isinstance(data, list):
                    return [item for item in data if isinstance(item, dict)]
            if isinstance(body, list):
                return [item for item in body if isinstance(item, dict)]
        if last_error:
            raise last_error
        return []


def organization_from_fixture(path: str | None) -> list[dict[str, Any]] | None:
    if not path:
        return None
    payload = load_json_file(path)
    data = payload.get("data")
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def oauth_from_fixture(path: str | None) -> list[dict[str, Any]] | None:
    if not path:
        return None
    payload = load_json_file(path)
    data = payload.get("data")
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def resolve_organization(organizations: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str]:
    if len(organizations) == 1:
        return organizations[0], "single_organization"
    personal = [org for org in organizations if org.get("is_personal") is True]
    if len(personal) == 1:
        return personal[0], "single_personal_organization"
    if not organizations:
        return None, "missing_organization"
    return None, "multiple_organizations_require_selection"


def provider_matches_target(provider: dict[str, Any], target_slug: str, target_name: str) -> bool:
    values = [
        str(provider.get("slug", "")),
        str(provider.get("name", "")),
        str(provider.get("description", "")),
    ]
    normalized_values = {normalize_slug(value) for value in values if value}
    return target_slug in normalized_values or normalize_slug(target_name) in normalized_values


def compact_publisher(publisher: dict[str, Any]) -> dict[str, Any]:
    return {
        "slug": publisher.get("slug"),
        "name": publisher.get("name"),
        "description": publisher.get("description"),
        "categories": publisher.get("categories") or [],
    }


def exact_match_from_publishers(
    publishers: list[dict[str, Any]],
    target_slug: str,
    target_name: str,
) -> dict[str, Any] | None:
    for publisher in publishers:
        slug = str(publisher.get("slug", ""))
        name = str(publisher.get("name", ""))
        if (
            normalize_slug(slug) == target_slug
            or normalize_slug(name) == normalize_slug(target_name)
        ):
            return publisher
    return None


def protected_endpoint_count(template: dict[str, Any]) -> int:
    endpoints = template.get("endpoints")
    if not isinstance(endpoints, list):
        return 0
    count = 0
    for endpoint in endpoints:
        if not isinstance(endpoint, dict):
            continue
        method = str(endpoint.get("method", "")).upper()
        path = str(endpoint.get("path", "")).lower()
        description = str(endpoint.get("description", "")).lower()
        is_destructive = method == "DELETE" or any(
            term in f"{path} {description}" for term in DESTRUCTIVE_TERMS
        )
        if is_destructive and endpoint.get("is_protected") is True:
            count += 1
    return count


def build_report(
    *,
    target: str,
    catalog_publishers: list[dict[str, Any]],
    fuzzy_matches: list[dict[str, Any]],
    exact_match: dict[str, Any] | None,
    asana_template: dict[str, Any] | None,
    organization: dict[str, Any] | None,
    organization_status: str,
    oauth_providers: list[dict[str, Any]],
    deployment_mode: str,
    update_existing: bool,
) -> dict[str, Any]:
    target_slug = normalize_slug(target)
    target_provider = next(
        (
            provider
            for provider in oauth_providers
            if provider_matches_target(provider, target_slug, target)
        ),
        None,
    )
    existing = [compact_publisher(exact_match)] if exact_match and not update_existing else []
    updated = [compact_publisher(exact_match)] if exact_match and update_existing else []

    blocked: list[dict[str, Any]] = []
    if organization is None:
        blocked.append(
            {
                "company": target,
                "slug": target_slug,
                "reason": organization_status,
                "next_action": "Select or create the deployment organization before deployment.",
            }
        )
    elif asana_template is None:
        blocked.append(
            {
                "company": target,
                "slug": target_slug,
                "reason": "missing_asana_template",
                "next_action": "Load the live Asana publisher before cloning commercial settings.",
            }
        )
    elif exact_match and not update_existing:
        pass
    elif target_provider is None:
        blocked.append(
            {
                "company": target,
                "slug": target_slug,
                "reason": "missing_target_oauth_provider",
                "next_action": (
                    "Create a target-specific OAuth provider or configure a valid user-token path."
                ),
            }
        )
    elif deployment_mode != "deploy":
        blocked.append(
            {
                "company": target,
                "slug": target_slug,
                "reason": "dry_run_only",
                "next_action": (
                    "Re-run with deployment_mode=deploy after reviewing the generated plan."
                ),
            }
        )

    deployed: list[dict[str, Any]] = []
    status = "ok" if deployed or existing or updated else "blocked"
    if not blocked and not existing and not updated:
        status = "ready"

    return {
        "status": status,
        "dry_run": deployment_mode != "deploy",
        "connectors": AVAILABLE_CONNECTORS,
        "target": {"name": target, "slug": target_slug},
        "catalog_guard": {
            "queried_all": True,
            "publisher_count": len(catalog_publishers),
            "fuzzy_search": True,
            "exact_lookup": True,
        },
        "fuzzy_matches": [compact_publisher(item) for item in fuzzy_matches],
        "exact_match": compact_publisher(exact_match) if exact_match else None,
        "template": (
            {
                "slug": asana_template.get("slug"),
                "billing_model": asana_template.get("billing_model"),
                "wallet_network_id": asana_template.get("wallet_network_id"),
                "protected_endpoint_count": protected_endpoint_count(asana_template),
            }
            if asana_template
            else None
        ),
        "organization": (
            {
                "id": organization.get("id"),
                "name": organization.get("name"),
                "status": organization_status,
            }
            if organization
            else {"id": None, "name": None, "status": organization_status}
        ),
        "oauth": {
            "provider_count": len(oauth_providers),
            "target_provider": target_provider.get("slug") if target_provider else None,
        },
        "deployed": deployed,
        "existing": existing,
        "updated": updated,
        "skipped": [],
        "blocked": blocked,
    }


def run_once(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    inputs = config.get("inputs") if isinstance(config.get("inputs"), dict) else {}
    target = (args.target or inputs.get("target") or "").strip()
    if not target:
        return {
            "status": "blocked",
            "reason": "missing_target",
            "connectors": AVAILABLE_CONNECTORS,
            "blocked": [
                {
                    "company": "",
                    "slug": "",
                    "reason": "missing_target",
                    "next_action": "Provide --target or inputs.target.",
                }
            ],
        }

    deployment_mode = args.deployment_mode or str(inputs.get("deployment_mode") or "dry-run")
    update_existing = bool(args.update_existing or inputs.get("update_existing", False))
    target_slug = normalize_slug(target)

    if args.catalog_fixture:
        catalog_payload = load_json_file(args.catalog_fixture)
        catalog_publishers = publisher_list(catalog_payload, key="publishers")
        fuzzy_matches = publisher_list(catalog_payload, key="search")
        exact_match = exact_match_from_publishers(catalog_publishers, target_slug, target)
    else:
        client = GatewayClient(args.gateway_url, runtime_api_key())
        catalog_publishers = client.list_publishers()
        fuzzy_matches = client.search_publishers(target)
        exact_match = client.get_publisher(target_slug)

    if args.asana_fixture:
        asana_template = load_json_file(args.asana_fixture)
    else:
        client = GatewayClient(args.gateway_url, runtime_api_key())
        asana_template = client.get_publisher("asana")

    fixture_orgs = organization_from_fixture(args.organizations_fixture)
    if fixture_orgs is not None:
        organizations = fixture_orgs
    else:
        client = GatewayClient(args.gateway_url, runtime_api_key())
        organizations = client.list_organizations()
    organization, organization_status = resolve_organization(organizations)

    fixture_oauth = oauth_from_fixture(args.oauth_providers_fixture)
    if fixture_oauth is not None:
        oauth_providers = fixture_oauth
    elif organization and organization.get("id"):
        client = GatewayClient(args.gateway_url, runtime_api_key())
        oauth_providers = client.list_oauth_providers(str(organization["id"]))
    else:
        oauth_providers = []

    return build_report(
        target=target,
        catalog_publishers=catalog_publishers,
        fuzzy_matches=fuzzy_matches,
        exact_match=exact_match,
        asana_template=asana_template,
        organization=organization,
        organization_status=organization_status,
        oauth_providers=oauth_providers,
        deployment_mode=deployment_mode,
        update_existing=update_existing,
    )


def main() -> int:
    args = parse_args()
    try:
        config = load_config(args.config)
        result = run_once(args, config)
    except FactoryError as exc:
        result = {
            "status": "blocked",
            "reason": "runtime_error",
            "error": str(exc),
            "connectors": AVAILABLE_CONNECTORS,
        }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
