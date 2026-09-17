"""HTTP 鉴权与授权（B1）。

**本文件里最重要的不是那几条 401/403 断言，而是 `test_every_write_route_is_classified`。**
权限表是可以写对的，但**新加的路由会绕过它** —— 除非有人逼着你分类。
那条测试从 OpenAPI 里枚举出全部写路由，逐条要求命中一条显式规则，
于是「新端点忘了配权限」会变成一条失败的测试，而不是一个静默裸奔的端点。

401 与 403 必须分开：前者是「换 token」，后者是「换角色」，前端提示不一样。
"""

from __future__ import annotations

import os

os.environ.setdefault("API_AUTH_TOKEN", "test-api-token")

import pytest
from fastapi.testclient import TestClient

from requirement_agent.api.app import app
from requirement_agent.api.auth import (
    ANALYZE,
    OPS,
    READ,
    REVIEW,
    REVERT,
    ROLE_SCOPES,
    SUBMIT,
    WRITE_SCOPE_RULES,
    has_scope,
    is_exempt,
    required_scope,
    resolve_role,
)
from requirement_agent.config.settings import settings

# 一个**不带任何默认头**的客户端 —— 用来测「没带凭证会怎样」。
anonymous = TestClient(app, raise_server_exceptions=False)


def _client(token: str) -> TestClient:
    return TestClient(app, headers={"Authorization": f"Bearer {token}"},
                      raise_server_exceptions=False)


# ── 核心安全网：权限表覆盖每条写路由 ──────────────────────────────────────


def test_every_write_route_is_classified() -> None:
    """**新增写端点若忘了配权限，这条会红。**

    枚举 OpenAPI 里所有非 GET 操作，逐条要求：要么在豁免清单里，要么命中
    `WRITE_SCOPE_RULES` 的一条**显式规则**。

    为什么需要它：`required_scope` 对未分类的写路由会**降级成「要 admin」并打警告**
    （fail-closed，不会裸奔），但那只是兜底 —— 真正的意图应该是有人**明确**决定过
    「这个端点要什么权限」。这条测试把「忘了决定」变成可见的失败。
    """
    spec = app.openapi()
    unclassified: list[str] = []
    for path, operations in spec.get("paths", {}).items():
        for method in operations:
            if method.upper() in ("GET", "HEAD", "OPTIONS"):
                continue
            if is_exempt(path):
                continue
            if not any(pattern.match(path) for pattern, _ in WRITE_SCOPE_RULES):
                unclassified.append(f"{method.upper()} {path}")

    assert not unclassified, (
        "以下写路由没有显式权限规则，请在 api/auth.py 的 WRITE_SCOPE_RULES 里分类：\n  "
        + "\n  ".join(sorted(unclassified))
    )


def test_read_routes_need_only_read() -> None:
    """GET 一律只读档次 —— 与路径无关。"""
    assert required_scope("GET", "/api/v1/requirements") == READ
    assert required_scope("GET", "/api/v1/ops/outbox") == READ


# ── 权限表本身 ────────────────────────────────────────────────────────────


def test_role_matrix() -> None:
    """三个角色的权限边界 —— 尤其是**谁能回滚、谁不能**。"""
    roles = {name: set(scopes) for name, scopes in ROLE_SCOPES.items()}
    assert roles["reviewer"] == {READ, ANALYZE, SUBMIT, REVIEW}
    assert roles["admin"] == {READ, ANALYZE, SUBMIT, REVIEW, REVERT, OPS}
    assert roles["system_worker"] == {READ, ANALYZE, OPS}

    # 高危档次的边界：只有 admin 能回滚
    assert REVERT not in roles["reviewer"]
    assert REVERT not in roles["system_worker"]
    # 后台任务不能替人裁决
    assert REVIEW not in roles["system_worker"]


def test_revert_needs_a_revert_scope_not_review() -> None:
    """回滚与裁决是**两个**档次 —— 不能因为都能改需求就合并。"""
    assert required_scope("POST", "/api/v1/requirements/REQ-000015/revert") == REVERT
    assert required_scope("POST", "/api/v1/reviews/submit") == REVIEW


def test_classification_of_representative_writes() -> None:
    cases = {
        ("POST", "/api/v1/requirements/submit"): SUBMIT,
        ("POST", "/api/v1/requirements/ingest"): SUBMIT,
        ("POST", "/api/v1/agent/chat/stream"): ANALYZE,
        ("POST", "/api/v1/conversations"): ANALYZE,
        ("PATCH", "/api/v1/feature-capabilities"): REVIEW,
        ("PATCH", "/api/v1/capabilities/1"): REVIEW,
        ("POST", "/api/v1/constraints/aliases"): REVIEW,
        ("PATCH", "/api/v1/requirements/relations/1"): REVIEW,
        ("PATCH", "/api/v1/requirement-titles/1"): REVIEW,
        ("POST", "/api/v1/requirements/REQ-1/titles"): REVIEW,
        ("POST", "/api/v1/ops/outbox/dead-letters/1/retry"): OPS,
        ("POST", "/api/v1/documents/1/reindex"): OPS,
    }
    for (method, path), expected in cases.items():
        assert required_scope(method, path) == expected, f"{method} {path}"


# ── 豁免 ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", [
    "/api/v1/channels/feishu/webhook",  # 走飞书自身验签，不能要求本系统 token
    "/api/v1/health", "/api/v1/health/db", "/api/v1/health/llm",
    "/static/js/app.js", "/ui", "/health", "/openapi.json",
])
def test_exempt_paths(path: str) -> None:
    assert is_exempt(path) is True
    assert required_scope("POST", path) is None


def test_feishu_webhook_is_reachable_without_token() -> None:
    """豁免不能只是「规则表里写着」—— 真的打一次。"""
    response = anonymous.post("/api/v1/channels/feishu/webhook", json={})
    # 未配置飞书凭据时是 503（渠道自身的拒绝），**不是 401** —— 说明鉴权放行了
    assert response.status_code != 401


def test_health_is_reachable_without_token() -> None:
    assert anonymous.get("/health").status_code == 200


def test_ui_page_is_reachable_without_token() -> None:
    """页面本身要能打开（否则连登录界面都出不来）；它的 API 调用再各自 401。"""
    assert anonymous.get("/ui").status_code == 200


# ── 401：没凭证 / 凭证无效 / 服务端没配 ───────────────────────────────────


def test_missing_token_is_401() -> None:
    response = anonymous.get("/api/v1/requirements")
    assert response.status_code == 401
    assert "token" in response.json()["detail"].lower()


def test_invalid_token_is_401() -> None:
    response = _client("definitely-not-a-valid-token").get("/api/v1/requirements")
    assert response.status_code == 401


def test_malformed_authorization_header_is_401() -> None:
    for value in ("test-api-token", "Basic abc", "Bearer", "Bearer   "):
        response = anonymous.get("/api/v1/requirements", headers={"Authorization": value})
        assert response.status_code == 401, value


def test_no_configured_tokens_denies_everything(monkeypatch) -> None:
    """**默认拒绝**：服务端一个 token 都没配时，受保护端点全部 401。

    这条是刻意选的默认值。反过来（没配就放行）会让「忘记配置」等于「没有鉴权」——
    而这正是 B1 之前的实际状态。
    """
    monkeypatch.setattr(settings, "api_auth_tokens", {})
    monkeypatch.setattr(settings, "api_auth_token", type(settings.api_auth_token)(""))
    assert resolve_role("Bearer anything") is None
    assert anonymous.get("/api/v1/requirements").status_code == 401


def test_unknown_role_in_config_denies(monkeypatch) -> None:
    """配置里写了不认识的角色名 → **拒绝**，而不是降级成某个角色。

    静默降级会让人以为配好了；全部拒绝会立刻在日志里看见 `auth_unknown_role`。
    """
    monkeypatch.setattr(settings, "api_auth_tokens", {"tok": "superuser"})
    assert resolve_role("Bearer tok") is None


# ── 401 与 403 的区分 ─────────────────────────────────────────────────────


def test_valid_token_but_insufficient_scope_is_403(monkeypatch) -> None:
    """**403 不是 401。** 身份有效、只是角色不够 —— 前端该提示「权限不足」而不是「重新登录」。"""
    monkeypatch.setattr(settings, "api_auth_tokens", {"tok-reviewer": "reviewer"})
    response = _client("tok-reviewer").post("/api/v1/requirements/REQ-000015/revert",
                                            json={"target_version": 1})
    assert response.status_code == 403
    body = response.json()
    assert body["role"] == "reviewer"
    assert body["required_scope"] == REVERT
    assert "reviewer" in body["detail"]


def test_reviewer_can_review(monkeypatch) -> None:
    """与上一条成对：reviewer 做**它该能做的**事不该被拦。

    用真实端点会改数据，所以这里只验证「过了鉴权那一层」—— 422（请求体不合法）
    说明请求已经走到了路由校验，鉴权是放行的。
    """
    monkeypatch.setattr(settings, "api_auth_tokens", {"tok-reviewer": "reviewer"})
    response = _client("tok-reviewer").post("/api/v1/reviews/submit", json={})
    assert response.status_code == 422


def test_system_worker_cannot_review(monkeypatch) -> None:
    monkeypatch.setattr(settings, "api_auth_tokens", {"tok-worker": "system_worker"})
    response = _client("tok-worker").post("/api/v1/reviews/submit", json={})
    assert response.status_code == 403


def test_system_worker_can_read_and_do_ops(monkeypatch) -> None:
    monkeypatch.setattr(settings, "api_auth_tokens", {"tok-worker": "system_worker"})
    client = _client("tok-worker")
    assert client.get("/api/v1/requirements").status_code == 200
    # 运维端点过了鉴权（404 是因为事件不存在，不是 401/403）
    assert client.post("/api/v1/ops/outbox/dead-letters/1/retry").status_code != 403


def test_admin_token_has_everything(monkeypatch) -> None:
    monkeypatch.setattr(settings, "api_auth_tokens", {"tok-admin": "admin"})
    assert has_scope(resolve_role("Bearer tok-admin"), REVERT) is True
    assert has_scope(resolve_role("Bearer tok-admin"), OPS) is True


def test_legacy_api_auth_token_acts_as_admin(monkeypatch) -> None:
    """兼容入口：老的 `API_AUTH_TOKEN` 等同于一个 admin token，既有部署不用改。"""
    monkeypatch.setattr(settings, "api_auth_tokens", {})
    monkeypatch.setattr(settings, "api_auth_token", type(settings.api_auth_token)("legacy-tok"))
    assert resolve_role("Bearer legacy-tok") == "admin"


# ── 错误响应形状 ──────────────────────────────────────────────────────────


def test_error_response_carries_request_id_and_scope() -> None:
    """401/403 要带 `request_id`（能对上服务端日志）与 `required_scope`（能自查缺什么）。"""
    response = anonymous.get("/api/v1/requirements")
    body = response.json()
    assert set(body) == {"detail", "request_id", "required_scope"}
    assert body["request_id"]
    assert response.headers.get("X-Request-ID") == body["request_id"]
    # 401 要带 WWW-Authenticate，符合 HTTP 惯例
    assert response.headers.get("WWW-Authenticate") == "Bearer"


def test_inbound_request_id_is_echoed() -> None:
    response = anonymous.get("/api/v1/requirements", headers={"X-Request-ID": "trace-me-123"})
    assert response.json()["request_id"] == "trace-me-123"


def test_auth_failures_are_logged(caplog) -> None:
    """失败要留痕 —— 否则「谁在拿错 token」只能靠猜。"""
    import logging

    with caplog.at_level(logging.WARNING, logger="requirement_agent.api.app"):
        anonymous.get("/api/v1/requirements")
    assert any("auth_unauthorized" in r.getMessage() for r in caplog.records)
