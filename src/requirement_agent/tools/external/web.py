"""Public external tools; requests are translated to Tavily MCP, not Tavily REST."""

from __future__ import annotations

import re
from typing import Any

from pydantic import Field, field_validator

from requirement_agent.infrastructure.mcp.tool_adapter import (
    TavilyToolAdapter,
    make_default_tavily_adapter,
)
from requirement_agent.tools.base import BaseTool, ToolInput, ToolResult
from requirement_agent.tools.registry import register

_MAX_QUERY_CHARS = 256


class WebSearchInput(ToolInput):
    query: str = Field(
        min_length=1,
        max_length=_MAX_QUERY_CHARS,
        description="A short, sanitized public-web search query; do not send internal source text.",
    )
    limit: int | None = Field(default=None, ge=1, le=10, description="Maximum result count.")

    @field_validator("query", mode="before")
    @classmethod
    def _sanitize_query(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return re.sub(r"[\x00-\x1f\x7f]+", " ", value).strip()


class ReadWebPageInput(ToolInput):
    url: str = Field(min_length=1, max_length=2_048, description="Public http(s) URL to extract.")


class _TavilyTool(BaseTool):
    def __init__(self, adapter: TavilyToolAdapter | None = None) -> None:
        self._adapter = adapter

    @property
    def adapter(self) -> TavilyToolAdapter:
        if self._adapter is None:
            self._adapter = make_default_tavily_adapter()
        return self._adapter


@register
class WebSearchTool(_TavilyTool):
    name = "web_search"
    description = (
        "Search public web sources through the configured Tavily MCP service. "
        "Use only a short query, never internal documents or credentials."
    )
    input_model = WebSearchInput
    output_schema: dict[str, Any] = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "url": {"type": "string"},
                "snippet": {"type": "string"},
                "score": {"type": ["number", "null"]},
            },
        },
    }
    allowed_consumers = ("assistant",)
    timeout_seconds = 60.0
    max_result_count = 10

    def execute(self, params: WebSearchInput) -> ToolResult:
        return self.adapter.web_search(params.query, limit=params.limit)


@register
class ReadWebPageTool(_TavilyTool):
    name = "read_web_page"
    description = "Read one validated public web page through the configured Tavily MCP service."
    input_model = ReadWebPageInput
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "url": {"type": "string"},
            "content": {"type": "string"},
            "truncated": {"type": "boolean"},
        },
    }
    allowed_consumers = ("assistant",)
    timeout_seconds = 60.0
    max_result_count = 1

    def execute(self, params: ReadWebPageInput) -> ToolResult:
        return self.adapter.read_web_page(params.url)
