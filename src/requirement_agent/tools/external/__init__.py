"""Read-only external research tools backed exclusively by Tavily MCP."""

from requirement_agent.tools.external.web import ReadWebPageTool, WebSearchTool

__all__ = ["ReadWebPageTool", "WebSearchTool"]
