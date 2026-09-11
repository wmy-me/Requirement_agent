"""MCP 静态 Bearer Token 校验（对齐 mcp SDK 的 TokenVerifier 协议）。"""

from __future__ import annotations

from mcp.server.auth.provider import AccessToken, TokenVerifier

from src.config.settings import settings


class StaticTokenVerifier(TokenVerifier):
    """校验请求 `Authorization: Bearer <token>` 是否等于配置的 MCP_AUTH_TOKEN。

    未配置 MCP_AUTH_TOKEN 时拒绝一切请求（fail-closed），保证 MCP 面默认安全。
    """

    async def verify_token(self, token: str) -> AccessToken | None:
        """校验 Bearer token：等于配置的 MCP_AUTH_TOKEN 则放行，否则返回 None 拒绝。

        `TokenVerifier.verify_token` 的抽象方法实现：MCP 会话初始化阶段被 SDK 调用。
        """
        expected = settings.mcp_auth_token.get_secret_value().strip()
        if not expected or token != expected:
            return None
        return AccessToken(
            token=token,
            client_id=settings.mcp_actor_id or "mcp-client",
            scopes=["mcp"],
            subject=settings.mcp_actor_id or "mcp-client",
        )


__all__ = ["StaticTokenVerifier"]
