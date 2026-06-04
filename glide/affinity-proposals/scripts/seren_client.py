from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


class PublisherError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class GatewayClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.serendb.com",
        user_agent: str = "glide-affinity-proposals",
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.user_agent = user_agent

    @classmethod
    def from_env(cls, *, skill_root: Path | None = None) -> "GatewayClient":
        api_key = os.environ.get("SEREN_API_KEY") or os.environ.get("API_KEY")
        if not api_key and skill_root:
            env_path = skill_root / ".env"
            if env_path.exists():
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    if line.startswith("SEREN_API_KEY="):
                        api_key = line.partition("=")[2].strip()
                        break
        if not api_key:
            raise RuntimeError("SEREN_API_KEY is required for publisher calls")
        return cls(api_key)

    def _request(
        self,
        method: str,
        url: str,
        *,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        response_format: str = "json",
    ) -> Any:
        encoded = json.dumps(body).encode("utf-8") if body is not None else None
        request_headers = {
            "Authorization": f"Bearer {self.api_key}",
            "User-Agent": self.user_agent,
        }
        if encoded is not None:
            request_headers["Content-Type"] = "application/json"
        if headers:
            request_headers.update(headers)
        request = urllib.request.Request(
            url,
            data=encoded,
            headers=request_headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request) as response:  # noqa: S310
                data = response.read()
                status = response.status
        except urllib.error.HTTPError as exc:
            data = exc.read()
            status = exc.code
        if not (200 <= status < 300):
            text = data.decode("utf-8", errors="replace")
            raise PublisherError(status, text[:1000])
        if response_format == "bytes":
            return data
        if not data:
            return {}
        decoded = json.loads(data)
        return _unwrap(decoded)

    def call_publisher(
        self,
        publisher: str,
        *,
        method: str = "GET",
        path: str = "/",
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        response_format: str = "json",
    ) -> Any:
        quoted_path = path if path.startswith("/") else "/" + path
        url = f"{self.base_url}/publishers/{publisher}{quoted_path}"
        return self._request(
            method,
            url,
            body=body,
            headers=headers,
            response_format=response_format,
        )

    def call_tool(self, publisher: str, tool: str, tool_args: dict[str, Any] | None = None) -> Any:
        # Hosted MCP publishers expose generated tool names through the
        # gateway. Keep this adapter isolated because individual
        # publishers differ in exact route generation.
        tool_args = tool_args or {}
        if publisher == "seren-passwords":
            if tool == "get_vaults":
                return self.call_publisher(publisher, method="GET", path="/vaults")
            if tool == "get_vaults_by_vault_id_items":
                return self.call_publisher(
                    publisher,
                    method="GET",
                    path=f"/vaults/{tool_args['vault_id']}/items",
                )
            if tool == "get_vaults_by_vault_id_items_by_item_id":
                return self.call_publisher(
                    publisher,
                    method="GET",
                    path=(
                        f"/vaults/{tool_args['vault_id']}/items/"
                        f"{tool_args['item_id']}"
                    ),
                )
        return self.call_publisher(
            publisher,
            method="POST",
            path=f"/tools/{urllib.parse.quote(tool)}",
            body=tool_args,
        )


    def chat_json(
        self,
        *,
        messages: list[dict[str, str]],
        response_schema: dict[str, Any],
        model: str = "anthropic/claude-sonnet-4-5",
        temperature: float = 0,
    ) -> dict[str, Any]:
        response = self.call_publisher(
            "seren-models",
            method="POST",
            path="/chat/completions",
            body={
                "model": model,
                "messages": messages,
                "response_schema": response_schema,
                "temperature": temperature,
            },
        )
        if isinstance(response, dict) and "choices" in response:
            content = response["choices"][0]["message"]["content"]
            return _loads_model_json(content) if isinstance(content, str) else content
        if isinstance(response, dict):
            return response
        raise RuntimeError("Model response was not a JSON object")


def _loads_model_json(content: str) -> Any:
    """Parse a JSON object out of seren-models chat content.

    The model returns chat text, not guaranteed-bare JSON: commonly a
    ```json fenced block, sometimes JSON with surrounding prose
    (`response_schema` is not enforced as structured output). Strip a
    surrounding code fence, then fall back to extracting the first
    balanced `{...}` object before parsing (issue #870).
    """

    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]  # drop opening ``` / ```json line
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]  # drop closing fence
        text = "\n".join(lines).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    obj = _first_json_object(text)
    if obj is None:
        raise RuntimeError("seren-models response did not contain a JSON object")
    return json.loads(obj)


def _first_json_object(text: str) -> str | None:
    """Return the first balanced top-level `{...}` substring, or None.

    String-aware so a `}` inside a quoted value does not close the
    object early.
    """

    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_str:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_str = False
        elif char == '"':
            in_str = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def _unwrap(payload: Any) -> Any:
    if isinstance(payload, dict) and set(payload.keys()) == {"data"}:
        inner = payload["data"]
        if isinstance(inner, dict) and "body" in inner and "status" in inner:
            return inner["body"]
        return inner
    return payload
