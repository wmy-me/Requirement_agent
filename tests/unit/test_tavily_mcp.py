"""Offline contract tests for the Tavily MCP boundary."""

from __future__ import annotations

from typing import Any

import pytest

from requirement_agent.config.mcp import TavilyMCPConfig
from requirement_agent.infrastructure.mcp.client_manager import MCPClientManager
from requirement_agent.infrastructure.mcp.tool_adapter import TavilyToolAdapter
from requirement_agent.tools import ToolStatus
from requirement_agent.tools.external.web import ReadWebPageTool, WebSearchTool


class FakeMCPTransport:
    def __init__(self, tools: list[str] | None = None, result: dict[str, Any] | None = None) -> None:
        self.tools = tools or ["tavily_search", "tavily_extract"]
        self.result = result or {"structuredContent": {"results": []}}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def discover_tools(self) -> list[dict[str, Any]]:
        return [{"name": name} for name in self.tools]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, arguments))
        return self.result

    def close(self) -> None:
        pass


def _config(*, enabled: bool = True) -> TavilyMCPConfig:
    return TavilyMCPConfig(TAVILY_MCP_ENABLED=enabled)


def _public_resolver(_hostname: str, _port: int | None) -> list[tuple[Any, ...]]:
    return [(None, None, None, None, ("8.8.8.8", 443))]


def _adapter(
    fake: FakeMCPTransport, *, enabled: bool = True
) -> TavilyToolAdapter:
    config = _config(enabled=enabled)
    manager = MCPClientManager(config, transport_factory=lambda _config, _key: fake)
    return TavilyToolAdapter(manager, config, resolver=_public_resolver)


def test_disabled_tavily_returns_visible_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    result = WebSearchTool(_adapter(FakeMCPTransport(), enabled=False)).run({"query": "roadmap"})
    assert result.status is ToolStatus.ERROR
    assert result.message == "Tavily MCP is disabled"


def test_missing_credential_returns_visible_error(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    result = WebSearchTool(_adapter(FakeMCPTransport())).run({"query": "roadmap"})
    assert result.status is ToolStatus.ERROR
    assert "credential is missing" in (result.message or "")


def test_credential_in_documented_dotenv_file_is_used_without_exporting_it(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The local `.env` convention must work even when the shell did not export it."""
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("TAVILY_API_KEY=local-test-key\n", encoding="utf-8")

    result = WebSearchTool(_adapter(FakeMCPTransport())).run({"query": "roadmap"})

    assert result.status is ToolStatus.EMPTY


def test_search_maps_only_to_allowlisted_mcp_tool_and_normalizes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    fake = FakeMCPTransport(
        tools=["tavily_search", "unrelated-admin-tool"],
        result={
            "structuredContent": {
                "results": [
                    {"title": "Release notes", "url": "https://example.com", "content": "Useful"}
                ]
            }
        },
    )
    result = WebSearchTool(_adapter(fake)).run({"query": " release\x00 notes ", "limit": 2})
    assert result.status is ToolStatus.SUCCESS
    assert result.result == [
        {"title": "Release notes", "url": "https://example.com", "snippet": "Useful", "score": None}
    ]
    assert fake.calls == [("tavily_search", {"query": "release  notes", "max_results": 2})]


def test_read_web_page_rejects_private_url_before_mcp_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    fake = FakeMCPTransport()
    result = ReadWebPageTool(_adapter(fake)).run({"url": "http://127.0.0.1/admin"})
    assert result.status is ToolStatus.ERROR
    assert "Unsafe web page URL" in (result.message or "")
    assert fake.calls == []


def test_read_web_page_rejects_unsafe_supplier_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    fake = FakeMCPTransport(
        result={
            "structuredContent": {
                "results": [
                    {"url": "http://127.0.0.1/private", "title": "redirected", "raw_content": "nope"}
                ]
            }
        }
    )
    result = ReadWebPageTool(_adapter(fake)).run({"url": "https://example.com/page"})
    assert result.status is ToolStatus.ERROR
    assert result.message == "Tavily extract returned an unsafe page URL"
    assert fake.calls == [("tavily_extract", {"urls": ["https://example.com/page"]})]


def test_legacy_hyphenated_allowlist_is_normalized() -> None:
    config = TavilyMCPConfig(TAVILY_MCP_ALLOWED_TOOLS="tavily-search,tavily-extract")
    assert config.allowed_tools == frozenset({"tavily_search", "tavily_extract"})


@pytest.mark.parametrize("result", [
    {"isError": True, "content": [{"type": "text", "text": "rate limited"}]},
    {"content": [{"type": "text", "text": "not json"}]},
])
def test_mcp_errors_and_unparseable_payloads_are_not_empty(monkeypatch: pytest.MonkeyPatch, result: dict[str, Any]) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    value = WebSearchTool(_adapter(FakeMCPTransport(result=result))).run({"query": "roadmap"})
    assert value.status is ToolStatus.ERROR


@pytest.mark.parametrize(
    "endpoint",
    ["http://mcp.tavily.com/mcp/", "https://localhost/mcp/", "https://mcp.tavily.com/other"],
)
def test_config_rejects_unsafe_endpoint(endpoint: str) -> None:
    with pytest.raises(ValueError, match="TAVILY_MCP_ENDPOINT"):
        TavilyMCPConfig(TAVILY_MCP_ENDPOINT=endpoint)
