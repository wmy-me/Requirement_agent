"""Translate the small Tavily MCP allowlist into stable tool results."""

from __future__ import annotations

import json
from typing import Any

from requirement_agent.config.mcp import TavilyMCPConfig
from requirement_agent.infrastructure.mcp.client_manager import (
    MCPClientManager,
    MCPUnavailableError,
)
from requirement_agent.infrastructure.mcp.url_safety import Resolver, validate_public_web_url
from requirement_agent.tools.base import ToolResult

_TOOL_MAP = {"web_search": "tavily_search", "read_web_page": "tavily_extract"}
_EXCERPT_CHARS = 1_500


class TavilyToolAdapter:
    """Only exposes the two public read-only tools, never arbitrary MCP methods."""

    def __init__(
        self,
        manager: MCPClientManager,
        config: TavilyMCPConfig,
        *,
        resolver: Resolver | None = None,
    ) -> None:
        self._manager = manager
        self._config = config
        self._resolver = resolver

    def web_search(self, query: str, *, limit: int | None = None) -> ToolResult:
        requested_limit = min(limit or self._config.result_limit, self._config.result_limit)
        raw = self._call("web_search", {"query": query, "max_results": requested_limit})
        if isinstance(raw, ToolResult):
            return raw
        rows = _rows(raw)
        if isinstance(rows, ToolResult):
            return rows
        normalized = [_search_row(row) for row in rows[:requested_limit]]
        if not normalized:
            return ToolResult.empty("Tavily search completed but returned no results")
        return ToolResult.success(normalized)

    def read_web_page(self, url: str) -> ToolResult:
        try:
            safe_url = validate_public_web_url(url, resolver=self._resolver)
        except ValueError as exc:
            return ToolResult.error(f"Unsafe web page URL: {exc}")
        raw = self._call("read_web_page", {"urls": [safe_url]})
        if isinstance(raw, ToolResult):
            return raw
        rows = _rows(raw)
        if isinstance(rows, ToolResult):
            return rows
        if not rows:
            return ToolResult.empty("Tavily extract completed but returned no page content")
        row = _page_row(rows[0], requested_url=safe_url, resolver=self._resolver)
        if row is None:
            return ToolResult.error("Tavily extract returned an unsafe page URL")
        return ToolResult.success(row)

    def _call(self, public_name: str, arguments: dict[str, Any]) -> dict[str, Any] | ToolResult:
        mapped_name = _TOOL_MAP[public_name]
        try:
            return self._manager.call_tool(mapped_name, arguments)
        except MCPUnavailableError as exc:
            return ToolResult.error(str(exc))


def make_default_tavily_adapter() -> TavilyToolAdapter:
    config = TavilyMCPConfig()
    return TavilyToolAdapter(MCPClientManager(config), config)


def _rows(raw: dict[str, Any]) -> list[dict[str, Any]] | ToolResult:
    payload = _content_payload(raw)
    if isinstance(payload, ToolResult):
        return payload
    results = payload.get("results") if isinstance(payload, dict) else None
    if isinstance(results, list):
        return [row for row in results if isinstance(row, dict)]
    if isinstance(payload, dict) and payload.get("url"):
        return [payload]
    return []


def _content_payload(raw: dict[str, Any]) -> dict[str, Any] | ToolResult:
    if raw.get("isError"):
        return ToolResult.error(_mcp_error_message(raw, "Tavily MCP returned an error"))
    if isinstance(raw.get("structuredContent"), dict):
        return raw["structuredContent"]
    if isinstance(raw.get("results"), list):
        return raw
    content = raw.get("content")
    if not isinstance(content, list):
        return raw
    text = "\n".join(
        str(item.get("text", ""))
        for item in content
        if isinstance(item, dict) and item.get("type") == "text"
    ).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return ToolResult.error("Tavily MCP returned unparseable text content")
    return parsed if isinstance(parsed, dict) else ToolResult.error("Tavily MCP returned an unsupported payload")


def _mcp_error_message(raw: dict[str, Any], fallback: str) -> str:
    content = raw.get("content")
    if isinstance(content, list):
        text = " ".join(str(item.get("text") or "") for item in content if isinstance(item, dict)).strip()
        if text:
            return text[:500]
    return str(raw.get("message") or raw.get("error") or fallback)[:500]


def _search_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": str(row.get("title") or ""),
        "url": str(row.get("url") or ""),
        "snippet": _text(row.get("content") or row.get("snippet")),
        "score": row.get("score"),
    }


def _page_row(
    row: dict[str, Any], *, requested_url: str, resolver: Resolver | None
) -> dict[str, Any] | None:
    returned_url = str(row.get("url") or requested_url)
    try:
        # A supplier-reported final URL is treated as a redirect target and must
        # pass the same public-address validation before it reaches consumers.
        safe_url = validate_public_web_url(returned_url, resolver=resolver)
    except ValueError:
        return None
    content = _text(row.get("raw_content") or row.get("content") or row.get("snippet"))
    return {
        "title": str(row.get("title") or ""),
        "url": safe_url,
        "content": content,
        "truncated": len(content) >= _EXCERPT_CHARS,
    }


def _text(value: object) -> str:
    text = str(value or "").strip()
    return text[:_EXCERPT_CHARS]


__all__ = ["TavilyToolAdapter", "make_default_tavily_adapter"]
