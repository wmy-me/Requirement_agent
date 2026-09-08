"""HTTP API 的轻量鉴权依赖。"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Header, HTTPException, status

from src.config.settings import settings


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    """表示通过 API Token 验证后的调用方。"""

    actor_id: str


def require_api_principal(authorization: str | None = Header(default=None)) -> AuthenticatedPrincipal:
    """校验 Bearer Token，并返回当前 API 调用方。"""

    expected = settings.api_auth_token.get_secret_value().strip()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="API_AUTH_TOKEN is not configured",
        )

    prefix = "Bearer "
    provided = ""
    if authorization and authorization.startswith(prefix):
        provided = authorization[len(prefix) :].strip()

    if provided != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API token",
        )

    return AuthenticatedPrincipal(actor_id=settings.api_actor_id)
