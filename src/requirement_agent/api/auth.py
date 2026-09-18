"""HTTP 鉴权与授权（B1）。

**角色由服务端从 token 解析，调用方不能自称。** 这是选「多 token → 角色」而不是
「单 token + `X-Actor-Role` 请求头」的原因：后者只要拿到 token 就能自称 admin，
等于只做了认证、没做授权。

## 六类权限档次

| 档次 | 含义 | 典型端点 |
|---|---|---|
| `read` | 只读查询 | 全部 GET |
| `analyze` | 触发分析与会话 | `/agent/chat/*`、`/conversations/*`、`/memory` |
| `submit` | 待审核写入 | `/requirements/submit`、`/requirements/ingest` |
| `review` | **人工裁决** | `/reviews/submit`、能力/条件/标题/关系的裁决 |
| `revert` | 回滚 | `/requirements/{key}/revert` |
| `ops` | 运维 | `/ops/*`、`/documents/{id}/reindex` |

## 三个角色

| 角色 | 权限 |
|---|---|
| `reviewer` | read + analyze + submit + **review** |
| `admin` | 全部六项 |
| `system_worker` | read + analyze + ops（**无 review / revert**） |

> ⚠️ **`review` 与 `revert` 是高危档次**：它们能改变正式需求库。
> `reviewer` 能裁决但不能回滚 —— 回滚是 append-only 的破坏性更强（它会产出一个
> 内容退回的新版本），只给 `admin`。

## 默认拒绝（两条）

1. **没配 token → 全部受保护端点 401**（不是「没配就放行」）。安全功能的默认值必须是
   拒绝；否则一次忘记配置就等于没有鉴权。
2. **没被分类的写路由 → 要求 `admin` 并打警告**。新加的路由不会因为「忘了配权限」
   而静默裸奔。`tests/integration/test_api_auth.py` 会断言**每条写路由都被显式分类**。
"""

from __future__ import annotations

import logging
import re
from typing import Final

from requirement_agent.config.settings import settings

logger = logging.getLogger(__name__)

__all__ = [
    "ANALYZE",
    "EXEMPT",
    "OPS",
    "READ",
    "REVIEW",
    "REVERT",
    "ROLE_SCOPES",
    "ROLES",
    "SUBMIT",
    "WRITE_SCOPE_RULES",
    "has_scope",
    "is_exempt",
    "required_scope",
    "resolve_role",
]

# ── 权限档次 ──────────────────────────────────────────────────────────────
READ: Final = "read"
ANALYZE: Final = "analyze"
SUBMIT: Final = "submit"
REVIEW: Final = "review"
REVERT: Final = "revert"
OPS: Final = "ops"

# `required_scope` 对豁免路径返回它，调用方据此跳过鉴权。
EXEMPT: Final = "exempt"

ROLES: Final = ("reviewer", "admin", "system_worker")

ROLE_SCOPES: Final[dict[str, frozenset[str]]] = {
    "reviewer": frozenset({READ, ANALYZE, SUBMIT, REVIEW}),
    "admin": frozenset({READ, ANALYZE, SUBMIT, REVIEW, REVERT, OPS}),
    # 后台任务：能查、能跑分析、能处理死信；**不能裁决、不能回滚**
    "system_worker": frozenset({READ, ANALYZE, OPS}),
}

# ── 豁免：不需要 API token 的路径 ─────────────────────────────────────────
# 飞书 Webhook 走飞书自身的签名 + Verification Token + AES 校验，**不能**再要求
# 本系统的 API token（飞书不会带它）。见 `docs/current-state.md` §四。
EXEMPT_PATTERNS: Final = (
    re.compile(r"^/api/v1/channels/feishu/webhook$"),
)
EXEMPT_PREFIXES: Final = (
    "/static",
    "/app/",  # 新工作台页面：// 与 /ui 同理，页面本身要能打开（数据请求仍要 token）
    "/api/v1/health",  # 探活：前端每 30 秒轮询，鉴权会让它变成噪声
)
EXEMPT_EXACT: Final = frozenset(
    {"/", "/ui", "/app", "/health", "/docs", "/redoc", "/openapi.json", "/favicon.ico"}
)

# ── 写路由 → 权限档次 ─────────────────────────────────────────────────────
# ⚠️ **顺序敏感：具体的规则必须排在笼统的前面**（如 `/requirements/{key}/revert`
# 要排在 `/requirements/...` 的其它规则之前）。
# 只对**非 GET**生效 —— GET 一律 `read`。
WRITE_SCOPE_RULES: Final = (
    (re.compile(r"^/api/v1/requirements/[^/]+/revert$"), REVERT),
    (re.compile(r"^/api/v1/requirements/submit(?:/async)?$"), SUBMIT),
    (re.compile(r"^/api/v1/requirements/ingest$"), SUBMIT),
    (re.compile(r"^/api/v1/requirements/relations/"), REVIEW),
    (re.compile(r"^/api/v1/requirements/[^/]+/titles$"), REVIEW),
    (re.compile(r"^/api/v1/reviews/"), REVIEW),
    (re.compile(r"^/api/v1/feature-capabilities$"), REVIEW),
    (re.compile(r"^/api/v1/capabilities"), REVIEW),
    (re.compile(r"^/api/v1/constraints"), REVIEW),
    (re.compile(r"^/api/v1/requirement-titles/"), REVIEW),
    (re.compile(r"^/api/v1/ops/"), OPS),
    (re.compile(r"^/api/v1/documents/[^/]+/reindex$"), OPS),
    (re.compile(r"^/api/v1/agent/"), ANALYZE),
    (re.compile(r"^/api/v1/conversations"), ANALYZE),
    (re.compile(r"^/api/v1/memory"), ANALYZE),
)

_SAFE_METHODS: Final = frozenset({"GET", "HEAD", "OPTIONS"})


def is_exempt(path: str) -> bool:
    if path in EXEMPT_EXACT:
        return True
    if path.startswith(EXEMPT_PREFIXES):
        return True
    return any(pattern.match(path) for pattern in EXEMPT_PATTERNS)


def required_scope(method: str, path: str) -> str | None:
    """这个请求需要哪个权限档次；`None` 表示豁免（不鉴权）。

    返回 `READ` 表示「已认证即可」（任何角色都有 `read`）。
    """
    if is_exempt(path):
        return None
    if method.upper() in _SAFE_METHODS:
        return READ
    for pattern, scope in WRITE_SCOPE_RULES:
        if pattern.match(path):
            return scope
    # ⚠️ 没分类的写路由：**要求 admin 并留警告**，而不是放行。
    # 新增路由的人可能忘了配权限；静默裸奔比「admin 才能调」危险得多。
    logger.warning(
        "event=auth_unclassified_write_route method=%s path=%s required=%s",
        method, path, "admin",
    )
    return REVERT  # 只有 admin 有这个档次 —— 等价于「要 admin」


def resolve_role(authorization: str | None) -> str | None:
    """从 `Authorization: Bearer <token>` 解析角色；解析不出返回 `None`。

    **默认拒绝**：没有配置任何 token 时返回 `None`（全部 401），而不是放行。
    """
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    if not token:
        return None

    configured = dict(settings.api_auth_tokens or {})
    # 兼容入口：老的 `API_AUTH_TOKEN` 等同于一个 admin token
    legacy = settings.api_auth_token.get_secret_value().strip()
    if legacy:
        configured.setdefault(legacy, "admin")

    role = configured.get(token)
    if role is None:
        return None
    if role not in ROLE_SCOPES:
        # 配置写错角色名时**不要静默降级成某个角色** —— 宁可全部拒绝，
        # 让人在日志里看见，也不要让人以为配好了。
        logger.warning("event=auth_unknown_role role=%s", role)
        return None
    return role


def has_scope(role: str | None, scope: str) -> bool:
    if role is None:
        return False
    return scope in ROLE_SCOPES.get(role, frozenset())
