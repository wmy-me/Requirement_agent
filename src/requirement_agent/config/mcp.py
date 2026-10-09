"""Strict configuration for the remote Tavily MCP server.

The API key is deliberately read from an environment variable at call time.  It
must never be encoded into the endpoint URL or emitted by this module.
"""

from __future__ import annotations

import json
from typing import Annotated, Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

TAVILY_MCP_ENDPOINT = "https://mcp.tavily.com/mcp/"
TAVILY_MCP_TOOLS = frozenset({"tavily-search", "tavily-extract"})


class TavilyMCPConfig(BaseSettings):
    """Configuration for Tavily's official, remote streamable-HTTP MCP service."""

    enabled: bool = Field(default=False, alias="TAVILY_MCP_ENABLED")
    transport: Literal["http"] = Field(default="http", alias="TAVILY_MCP_TRANSPORT")
    endpoint: str = Field(default=TAVILY_MCP_ENDPOINT, alias="TAVILY_MCP_ENDPOINT")
    credential_env: str = Field(default="TAVILY_API_KEY", alias="TAVILY_MCP_CREDENTIAL_ENV")
    allowed_tools: Annotated[frozenset[str], NoDecode] = Field(
        default_factory=lambda: frozenset(TAVILY_MCP_TOOLS),
        alias="TAVILY_MCP_ALLOWED_TOOLS",
    )
    timeout_seconds: float = Field(default=20.0, gt=0, le=60, alias="TAVILY_MCP_TIMEOUT_SECONDS")
    result_limit: int = Field(default=5, ge=1, le=10, alias="TAVILY_MCP_RESULT_LIMIT")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("allowed_tools", mode="before")
    @classmethod
    def _parse_allowed_tools(cls, value: object) -> object:
        if isinstance(value, str):
            if value.lstrip().startswith("["):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError as exc:
                    raise ValueError("TAVILY_MCP_ALLOWED_TOOLS must be JSON or comma-separated") from exc
            else:
                value = [part.strip() for part in value.split(",") if part.strip()]
        return value

    @field_validator("endpoint")
    @classmethod
    def _validate_endpoint(cls, value: str) -> str:
        parsed = urlsplit(value.strip())
        if (
            parsed.scheme != "https"
            or parsed.hostname != "mcp.tavily.com"
            or parsed.port not in (None, 443)
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path.rstrip("/") != "/mcp"
        ):
            raise ValueError("TAVILY_MCP_ENDPOINT must be the HTTPS mcp.tavily.com/mcp endpoint")
        return urlunsplit(("https", "mcp.tavily.com", "/mcp/", "", ""))

    @field_validator("credential_env")
    @classmethod
    def _validate_credential_env(cls, value: str) -> str:
        if value.strip() != "TAVILY_API_KEY":
            raise ValueError("TAVILY_MCP_CREDENTIAL_ENV must be TAVILY_API_KEY")
        return "TAVILY_API_KEY"

    @model_validator(mode="after")
    def _validate_tool_allowlist(self) -> "TavilyMCPConfig":
        if not self.allowed_tools or not self.allowed_tools <= TAVILY_MCP_TOOLS:
            raise ValueError(
                "TAVILY_MCP_ALLOWED_TOOLS must be a non-empty subset of "
                "tavily-search,tavily-extract"
            )
        return self


__all__ = ["TAVILY_MCP_ENDPOINT", "TAVILY_MCP_TOOLS", "TavilyMCPConfig"]
