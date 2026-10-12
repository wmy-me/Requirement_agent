"""A small, injectable client for the official remote Tavily MCP service.

It speaks MCP JSON-RPC over streamable HTTP directly instead of depending on an
optional MCP SDK.  The transport is deliberately narrow: only discovery and
tool calls are needed by the read-only adapter.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from requirement_agent.config.mcp import TavilyMCPConfig


class MCPTransport(Protocol):
    def discover_tools(self) -> list[dict[str, Any]]: ...

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...

    def close(self) -> None: ...


TransportFactory = Callable[[TavilyMCPConfig, str], MCPTransport]


class MCPUnavailableError(RuntimeError):
    """The configured MCP service cannot safely be called."""


class MCPClientManager:
    """Manages one Tavily MCP transport and an allowlisted discovery cache."""

    def __init__(
        self,
        config: TavilyMCPConfig,
        *,
        transport_factory: TransportFactory | None = None,
    ) -> None:
        self.config = config
        self._factory = transport_factory or _http_transport
        self._transport: MCPTransport | None = None
        self._discovered: frozenset[str] | None = None

    def discover_tools(self) -> frozenset[str]:
        if self._discovered is None:
            transport = self._get_transport()
            try:
                raw_tools = transport.discover_tools()
            except Exception as exc:  # noqa: BLE001 - converted into tool-visible failure
                raise MCPUnavailableError("Tavily MCP is unavailable") from exc
            names = {
                str(tool.get("name"))
                for tool in raw_tools
                if isinstance(tool, dict) and isinstance(tool.get("name"), str)
            }
            self._discovered = frozenset(names & self.config.allowed_tools)
        return self._discovered

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name not in self.config.allowed_tools:
            raise MCPUnavailableError(f"Tavily MCP tool {name!r} is not allowlisted")
        if name not in self.discover_tools():
            raise MCPUnavailableError(f"Tavily MCP did not expose allowlisted tool {name!r}")
        try:
            return self._get_transport().call_tool(name, arguments)
        except MCPUnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001 - must not expose credentials or endpoint details
            raise MCPUnavailableError("Tavily MCP is unavailable") from exc

    def close(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None
            self._discovered = None

    def _get_transport(self) -> MCPTransport:
        if not self.config.enabled:
            raise MCPUnavailableError("Tavily MCP is disabled")
        credential = _credential_from_environment_or_dotenv(self.config.credential_env)
        if not credential:
            raise MCPUnavailableError(
                f"Tavily MCP credential is missing: set {self.config.credential_env}"
            )
        if self._transport is None:
            try:
                self._transport = self._factory(self.config, credential)
            except Exception as exc:  # noqa: BLE001
                raise MCPUnavailableError("Tavily MCP is unavailable") from exc
        return self._transport


class StreamableHTTPMCPTransport:
    """Minimal synchronous MCP streamable-HTTP transport.

    Redirects are disabled.  Authentication stays in an HTTP header, never in
    the endpoint URL.  No endpoint or authorization value is logged here.
    """

    def __init__(self, config: TavilyMCPConfig, credential: str) -> None:
        import httpx

        self._httpx = httpx
        self._client = httpx.Client(
            timeout=config.timeout_seconds,
            follow_redirects=False,
            headers={
                "Authorization": f"Bearer {credential}",
                "Accept": "application/json, text/event-stream",
            },
        )
        self._endpoint = config.endpoint
        self._session_id: str | None = None
        self._request_id = 0
        self._initialized = False

    def discover_tools(self) -> list[dict[str, Any]]:
        result = self._request("tools/list", {})
        tools = result.get("tools")
        if not isinstance(tools, list):
            raise MCPUnavailableError("Tavily MCP returned an invalid tools list")
        return [tool for tool in tools if isinstance(tool, dict)]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._request("tools/call", {"name": name, "arguments": arguments})

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._initialize()
        self._request_id += 1
        return self._post({"jsonrpc": "2.0", "id": self._request_id, "method": method, "params": params})

    def _initialize(self) -> None:
        if self._initialized:
            return
        self._request_id += 1
        self._post(
            {
                "jsonrpc": "2.0",
                "id": self._request_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "requirement-agent", "version": "0.1.0"},
                },
            }
        )
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        self._initialized = True

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        response = self._client.post(self._endpoint, json=payload, headers=headers)
        if response.is_redirect:
            raise MCPUnavailableError("Tavily MCP redirect was refused")
        response.raise_for_status()
        self._session_id = response.headers.get("Mcp-Session-Id", self._session_id)
        if response.status_code == 202 or not response.content:
            return {}
        data = _decode_mcp_response(response)
        if "error" in data:
            raise MCPUnavailableError("Tavily MCP returned an error")
        result = data.get("result", data)
        if not isinstance(result, dict):
            raise MCPUnavailableError("Tavily MCP returned an invalid response")
        return result


def _decode_mcp_response(response: Any) -> dict[str, Any]:
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" not in content_type:
        parsed = response.json()
        if not isinstance(parsed, dict):
            raise MCPUnavailableError("Tavily MCP returned an invalid response")
        return parsed
    for block in response.text.split("\n\n"):
        data_lines = [line[5:].strip() for line in block.splitlines() if line.startswith("data:")]
        if data_lines:
            parsed = json.loads("\n".join(data_lines))
            if isinstance(parsed, dict):
                return parsed
    raise MCPUnavailableError("Tavily MCP returned an empty event stream")


def _http_transport(config: TavilyMCPConfig, credential: str) -> MCPTransport:
    return StreamableHTTPMCPTransport(config, credential)


def _credential_from_environment_or_dotenv(name: str) -> str:
    """Read a credential at call time without ever logging or exporting it.

    Production deploys commonly inject environment variables.  Local development
    uses the repository's documented `.env`; Pydantic reads that file for normal
    settings but deliberately does not mutate ``os.environ``.  The old client
    therefore treated a valid local `TAVILY_API_KEY` as absent.  Keep the real
    process environment authoritative and use `.env` only as a local fallback.
    """
    value = os.environ.get(name, "").strip()
    if value:
        return value
    try:
        from dotenv import dotenv_values

        return str(dotenv_values(Path(".env")).get(name) or "").strip()
    except (OSError, ValueError):
        return ""


__all__ = [
    "MCPClientManager",
    "MCPTransport",
    "MCPUnavailableError",
    "StreamableHTTPMCPTransport",
]
