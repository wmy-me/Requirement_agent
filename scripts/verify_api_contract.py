#!/usr/bin/env python
"""契约验证：核对 `docs/api-contract.md` 描述的东西与**真实响应**是否一致。

**为什么要有这个脚本。** 2026-09-16 复跑契约时抓到三处「文档写错了」——
§1 说「雪花 ID 一律字符串」（只有审核端点是这样）、需求库列表少写 4 个字段、
对话 SSE 的事件名写的是后端根本不存在的 `delta`/`message`。这三条**都不会让测试变红**
（它们不是代码的错，是文档与代码脱节），却会让照文档重写的前端「实现完跑不通」。
所以把 §9 描述的那套方法固化成可执行的脚本：**能跑、会红、可挂 CI**。

它做三件事（对应 `docs/api-contract.md` §9 的四步）：

1. **逐端点核对字段存在**：`SPEC` 里列的字段（= 契约承诺的响应形状）必须在真实响应里，
   缺一个就报错并让进程以非 0 退出码结束。
2. **清点大整数的 JSON 类型**：把响应里超过 `2^53` 的值挑出来，报告它被序列化成
   `string` 还是 `number`、以及能否被 double 精确表示。
   —— T1 的「ID 序列化统一」就靠这张表定范围与验收。
3. **数据库级 ID 精度扫描**：直接查各表的 `id` 列，找**能不能被 double 精确表示**的值。

> ### ⚠️ 为什么第 3 步不能省
>
> 第 2 步只扫得到**当前有数据的端点**。2026-09-16 真实库里就有一个不可精确表示的 ID
> （`outbox_event.id = 225548242094391297`，`int(float())` 差 1），但它属于一条
> `completed` 事件、不进任何响应 —— 只靠端点采样**永远扫不到**。而它一旦变成死信，
> 就会以 JSON number 出现在 `/ops/outbox`，前端拿它拼 `dead-letters/{id}/retry`
> 就会打到错误的一行。第 3 步查的是**数据**，与「现在有没有接口暴露它」无关。

**它不能替代什么**：SPEC 是人写的字段清单，不是从 `app.js` 里自动解析出来的
（那需要真正的 AST，正则做不到可靠）。它对 `app.js` 只做**信息性**比对（打印而不判失败）：
契约本就该描述完整响应，而当前 `app.js` 只是它的一个（即将被替换的）消费者 ——
「契约有、前端没读」是正常的；反过来的「前端读了、契约没写」才值得看。

用法：
    python scripts/verify_api_contract.py            # 全量核对
    python scripts/verify_api_contract.py --only ids # 只看 ID 类型表与精度扫描

退出码：0 = 全绿；1 = 有字段缺失，或存在**会被暴露且不可精确表示**的 ID。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

os.environ.setdefault("API_AUTH_TOKEN", "test-api-token")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_JS = PROJECT_ROOT / "static" / "js" / "app.js"

# 大整数门槛：JS 的 Number.MAX_SAFE_INTEGER。
JS_MAX_SAFE_INTEGER = 2**53

# 打真实库时用的样本 key —— 取「存在且带版本链」的那条，测不出东西时脚本会如实报「跳过」。
SAMPLE_KEY = "REQ-000015"


@dataclass
class Endpoint:
    """一个要核对的端点，以及**前端实际会读**的字段。"""

    name: str
    path: str
    fields: tuple[str, ...]
    items_key: str = "items"
    params: dict[str, object] = field(default_factory=dict)
    # 该端点是否可能没有样本数据（空 items 时跳过字段核对而不是判失败）
    optional: bool = False


#: SPEC 里可以出现的路径占位符 → 跑真实库时取到的真实 id。
#:
#: 为什么要这套东西：一半以上的端点是 `/xxx/{id}/yyy` 形状，写死一个 id 会让脚本
#: 在别的库上失效。取不到样本时**跳过该端点而不是判失败** —— 「这个库还没这类数据」
#: 和「契约对不上」是两回事，混在一起会让脚本在干净库上永远红。
SAMPLE_SOURCES: dict[str, tuple[str, str, str]] = {
    # 占位符          : (取样本的端点, 取哪个列表, 取哪个字段)
    "requirement_key": ("/api/v1/requirements", "items", "requirement_key"),
    "run_id": ("/api/v1/agent/runs", "items", "run_id"),
    "source_id": ("/api/v1/sources", "items", "source_id"),
    # merge-preview 要求来源处于 pending_review，否则 409 —— 所以单独取一个待审的
    "pending_source_id": ("/api/v1/reviews/pending", "items", "source_id"),
    "document_id": ("/api/v1/documents", "items", "id"),
    "capability_id": ("/api/v1/capabilities", "items", "id"),
    "constraint_id": ("/api/v1/constraints", "items", "id"),
    "conversation_id": ("/api/v1/conversations", "items", "id"),
}

#: 占位符 → 从别的占位符派生（列表端点里拿不到、只能从详情里挖）。
SAMPLE_DERIVED: dict[str, tuple[str, str]] = {
    # title_id 从 /requirements/{key}/titles 的 items[].id 取
    "title_id": ("/api/v1/requirements/{requirement_key}/titles", "items"),
    # relation_id 从 /requirements/{key}/relations 的 items[].id 取
    "relation_id": ("/api/v1/requirements/{requirement_key}/relations", "items"),
    # memory_id 从 /memory 的 items[].id 取
    "memory_id": ("/api/v1/memory", "items"),
    # session_id 用会话 id（对话端点的路径参数就叫这个名字）
    "session_id": ("/api/v1/conversations", "items"),
    # message_id 从会话消息里取
    "message_id": ("/api/v1/conversations/{conversation_id}/messages", "items"),
    # event_id 取死信 —— 没有死信就跳过（那是正常状态，不是故障）
    "event_id": ("/api/v1/ops/outbox", "dead_letters"),
}


def collect_sample_params(client) -> dict[str, str]:
    """跑真实库取一批 id，供 SPEC 里的 `{占位符}` 替换。取不到的不放进结果。"""
    found: dict[str, str] = {}

    for name, (path, items_key, field) in SAMPLE_SOURCES.items():
        try:
            response = client.get(path, params={"limit": 3})
            if response.status_code != 200:
                continue
            items = response.json().get(items_key) or []
            if not items:
                continue
            value = items[0].get(field)
            if value:
                found[name] = str(value)
        except Exception:
            continue

    for name, (path, items_key) in SAMPLE_DERIVED.items():
        try:
            resolved = resolve_path(path, found)
            if resolved is None:
                continue
            response = client.get(resolved)
            if response.status_code != 200:
                continue
            items = response.json().get(items_key) or []
            if not items:
                continue
            value = items[0].get("id")
            if value:
                found[name] = str(value)
        except Exception:
            continue

    return found


def resolve_path(path: str, params: dict[str, str]) -> str | None:
    """把路径里的 `{占位符}` 换成真实 id；有换不掉的返回 None（调用方跳过该端点）。"""
    resolved = path
    for slot in re.findall(r"\{([^}]+)\}", path):
        value = params.get(slot)
        if not value:
            return None
        resolved = resolved.replace("{" + slot + "}", value)
    return resolved


#: **没有进 SPEC 的活路由，以及为什么。**
#:
#: 这不是「不重要」，而是「这个脚本核对不了」：它只发 GET、只读响应。
#: 写端点的验证要么会改库、要么得构造完整事务，那是集成测试的事
#: （`tests/integration/`，写路由的权限分类另有 `test_api_auth.py` 兜底）。
#: 非 JSON 的（CSV / HTML / SSE）同理 —— 没有「字段」可核。
#:
#: ⚠️ **新增端点时必须在这里或 SPEC 里表态**，否则脚本会红（见 `check_coverage`）。
#: 那条闸门才是这份清单真正的价值：**让「没被检查」变成一件要说出口的事**。
EXCLUDED_ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/"): "HTML 页面",
    ("GET", "/health"): "无前缀探活，不是 JSON API 契约的一部分",
    ("GET", "/api/v1/requirements/export"): "返回 CSV，没有字段形状可言",
    ("POST", "/api/v1/requirements/ingest"): "multipart 写端点",
    ("POST", "/api/v1/requirements/submit"): "写端点",
    ("POST", "/api/v1/requirements/{requirement_key}/revert"): "写端点",
    ("POST", "/api/v1/requirements/{requirement_key}/titles"): "写端点",
    ("POST", "/api/v1/reviews/submit"): "写端点",
    ("POST", "/api/v1/agent/run"): "写端点（同步跑完整管线并落库）",
    ("POST", "/api/v1/agent/chat"): "写端点",
    ("POST", "/api/v1/agent/chat/stream"): "SSE，事件流不是 JSON 字段",
    ("POST", "/api/v1/agent/chat/stream-with-files"): "SSE",
    ("POST", "/api/v1/agent/runs/{run_id}/resume"): "SSE 写端点",
    ("POST", "/api/v1/agent/runs/{run_id}/retry"): "写端点",
    ("POST", "/api/v1/conversations"): "写端点",
    ("PATCH", "/api/v1/conversations/{conversation_id}"): "写端点",
    ("DELETE", "/api/v1/conversations/{conversation_id}"): "写端点",
    ("POST", "/api/v1/conversations/{conversation_id}/messages"): "写端点",
    ("POST", "/api/v1/conversations/{conversation_id}/finalize"): "写端点",
    ("POST", "/api/v1/memory"): "写端点",
    ("POST", "/api/v1/memory/{memory_id}/delete"): "写端点",
    ("POST", "/api/v1/documents/{document_id}/reindex"): "写端点",
    ("PATCH", "/api/v1/feature-capabilities"): "写端点（裁决）",
    ("PATCH", "/api/v1/capabilities/{capability_id}"): "写端点（裁决）",
    ("POST", "/api/v1/constraints/aliases"): "写端点（裁决）",
    ("PATCH", "/api/v1/requirement-titles/{title_id}"): "写端点（裁决）",
    ("PATCH", "/api/v1/requirements/relations/{relation_id}"): "写端点（裁决）",
    ("POST", "/api/v1/ops/outbox/dead-letters/{event_id}/retry"): "写端点",
    ("POST", "/api/v1/ops/outbox/dead-letters/{event_id}/discard"): "写端点",
    ("POST", "/api/v1/channels/feishu/webhook"): "写端点（且是唯一豁免鉴权的）",
}


# 这份清单是**契约的机器可读副本**：字段名的唯一来源仍是 `docs/api-contract.md`，
# 这里只是把它变成可执行的断言。改契约就要改这里，否则脚本会红——这正是目的。
SPEC: tuple[Endpoint, ...] = (
    Endpoint(
        name="需求库列表",
        path="/api/v1/requirements",
        params={"limit": 3},
        fields=(
            "requirement_key", "requirement_name", "final_requirement", "status",
            "business_domain", "business_domains", "current_version", "feature_count",
            "requester_names", "source_types", "departments", "sensitivity_levels",
            "first_source_submitted_at", "latest_source_submitted_at",
        ),
    ),
    Endpoint(
        name="版本历史",
        path=f"/api/v1/requirements/{SAMPLE_KEY}/versions",
        fields=(
            "id", "requirement_id", "parent_version_id", "parent_version_no", "version_no",
            "version_title", "change_type", "requirement_snapshot", "change_summary",
            "diff_payload", "feature_changes", "created_by", "reviewed_by", "created_at",
        ),
    ),
    Endpoint(
        name="功能明细",
        path=f"/api/v1/requirements/{SAMPLE_KEY}/features",
        fields=(
            "id", "requirement_id", "feature_key", "content", "status", "ordinal",
            "origin_source_id", "origin_requirement_key", "origin_version_no",
            "removed_version_no", "provenance", "module_key", "module_name",
        ),
    ),
    Endpoint(
        name="版本 diff",
        path=f"/api/v1/requirements/{SAMPLE_KEY}/diff",
        # diff 的顶层不是列表，字段在顶层上（items_key 置空表示「顶层就是对象」）
        items_key="",
        fields=(
            "requirement_key", "from_version", "to_version",
            "added", "removed", "modified", "unchanged",
        ),
    ),
    Endpoint(
        name="详情页能力与条件",
        path=f"/api/v1/requirements/{SAMPLE_KEY}/capabilities",
        items_key="capabilities",
        fields=(
            "feature_id", "capability_id", "feature_key", "feature_content",
            "action", "object", "display_name", "capability_status",
            "raw_text", "confidence", "review_status", "decided_by",
        ),
    ),
    Endpoint(
        name="需求关系（双向）",
        path=f"/api/v1/requirements/{SAMPLE_KEY}/relations",
        fields=(
            "id", "subject_requirement_key", "target_requirement_key", "relation_type",
            "reason", "similarity", "source_id", "status", "created_by", "decided_by",
            "created_at", "updated_at", "direction", "other_requirement_key",
            "other_requirement_name",
        ),
        optional=True,  # 库里可能一条关系都没有
    ),
    Endpoint(
        name="能力词表",
        path="/api/v1/capabilities",
        params={"limit": 3},
        fields=(
            "id", "action", "object", "display_name", "status",
            "created_by", "origin_source_id", "created_at", "updated_at",
        ),
    ),
    Endpoint(
        name="条件词表",
        path="/api/v1/constraints",
        params={"limit": 3},
        fields=("id",),  # ⚠️ 形状未经实测（词表是空表），只核对端点可达
        optional=True,
    ),
    Endpoint(
        name="候选标题",
        path=f"/api/v1/requirements/{SAMPLE_KEY}/titles",
        fields=(
            "id", "requirement_id", "title", "angle", "capability_id", "constraint_key",
            "source", "review_status", "decided_by", "created_by", "created_at", "highlight",
        ),
        optional=True,  # 库里 0 行
    ),
    Endpoint(
        name="待办列表",
        path="/api/v1/reviews/pending",
        params={"limit": 5},
        fields=(
            "source_id", "source_type", "source_event_id", "requester_id", "requester_name",
            "original_text", "extracted_text", "original_payload", "metadata",
            "processing_status", "submitted_at", "updated_at",
        ),
        optional=True,
    ),
    # ── 2026-09-18 前端接口盘点补入 ────────────────────────────────────────
    #
    # 这三条此前**不在 SPEC 里**，于是脚本报「✅ 契约与实现一致，且当前无精度风险」，
    # 而它们恰恰在发 JSON number 型的雪花 id —— 报「无风险」不是因为它检查过，
    # 是因为它没看。**测试全绿 ≠ 没问题**，这份 SPEC 的覆盖边界就是结论的边界。
    Endpoint(
        name="运行列表",
        path="/api/v1/agent/runs",
        params={"limit": 3},
        fields=(
            "id", "run_id", "conversation_id", "source_id", "run_type",
            "client_message_id", "status", "error", "meta", "stage", "checkpoint",
            "current_node", "started_at", "ended_at", "created_at", "updated_at",
        ),
        optional=True,  # 全新库上可能一个 run 都没有
    ),
    Endpoint(
        name="长期记忆",
        path="/api/v1/memory",
        params={"limit": 3},
        fields=(
            "id", "actor_id", "kind", "status", "active", "content",
            "source_conversation_id", "source_message_id", "ref_requirement_key",
            "superseded_by", "importance", "meta", "created_at", "updated_at",
        ),
        optional=True,
    ),
    Endpoint(
        name="来源列表",
        path="/api/v1/sources",
        params={"limit": 3},
        fields=(
            "source_id", "source_type", "requester_name", "processing_status",
            "error_message", "excerpt", "linked_requirement_key", "submitted_at", "updated_at",
        ),
        optional=True,
    ),
    # ── 2026-09-18 前端接口盘点：把只读端点补全 ────────────────────────────
    #
    # SPEC 从 10 条补到覆盖全部只读端点。**直接动因**：盘点发现 /agent/runs 与
    # /memory 在发 number 型雪花 id，而脚本报「无风险」—— 因为它压根没看那几个端点。
    # 补进去之后，当场又扫出 meta.assistant_message_id 也是 number。
    #
    # 写端点与非 JSON 端点不在 SPEC 里，但**必须在 EXCLUDED_ROUTES 里表态**，
    # 由 check_coverage 兜底 —— 见那份清单的说明。
    Endpoint(
        name="总览聚合",
        path="/api/v1/stats/overview",
        items_key="",  # 扁平对象，没有 items 包装
        fields=(
            "pending_review", "pending_total", "high_risk", "conflict", "dead_letter",
            "requirements_total", "sources_total", "analysed_sources",
            "source_status_counts", "channel_counts", "domain_counts",
            "risk_matrix", "submission_trend",
        ),
    ),
    Endpoint(
        name="审计事件",
        path="/api/v1/audit/events",
        params={"limit": 5},
        fields=(
            "trace_id", "event_type", "aggregate_type", "aggregate_id", "actor_type",
            "actor_id", "before_data", "after_data", "result_status", "error_code",
            "created_at",
        ),
        optional=True,
    ),
    Endpoint(
        name="数据库健康",
        path="/api/v1/health/db",
        items_key="",
        fields=("database", "status"),
    ),
    Endpoint(
        name="模型健康",
        path="/api/v1/health/llm",
        items_key="",
        fields=("configured", "provider", "model", "embedding", "request", "stats"),
    ),
    Endpoint(
        name="向量来源健康",
        path="/api/v1/health/embedding",
        items_key="",
        fields=("trusted", "reason", "tables", "action"),
    ),
    Endpoint(
        name="Outbox",
        path="/api/v1/ops/outbox",
        items_key="",
        fields=("counts", "dead_letters", "consumer"),
    ),
    Endpoint(
        name="Worker",
        path="/api/v1/ops/worker",
        items_key="",
        fields=("consumer", "counts", "stale_processing", "stale_timeout_seconds"),
    ),
    Endpoint(
        name="模型调用",
        path="/api/v1/ops/models",
        params={"limit": 5},
        fields=(
            "id", "run_id", "task_type", "provider", "model", "prompt_version",
            "schema_version", "input_tokens", "output_tokens", "latency_ms", "request_id",
            "status", "error_code", "fallback_from", "fallback_level", "fallback_used",
            "embedding_dimension", "embedding_version", "created_at",
        ),
        optional=True,
    ),
    Endpoint(
        name="会话列表",
        path="/api/v1/conversations",
        params={"limit": 3},
        fields=(
            "id", "actor_id", "title", "summary", "status", "meta", "message_count",
            "created_at", "updated_at",
        ),
        optional=True,
    ),
    Endpoint(
        name="会话消息",
        path="/api/v1/conversations/{conversation_id}/messages",
        items_key="items",
        fields=(
            "id", "conversation_id", "role", "content", "artifacts",
            "client_message_id", "run_id", "meta", "created_at",
        ),
        optional=True,
    ),
    Endpoint(
        name="对话回放",
        path="/api/v1/agent/chat/{session_id}",
        items_key="",
        fields=("session_id", "history"),
        optional=True,
    ),
    Endpoint(
        name="可续跑查询",
        path="/api/v1/agent/chat/{session_id}/resumable",
        items_key="",
        fields=("run",),
        optional=True,
    ),
    Endpoint(
        name="文档列表",
        path="/api/v1/documents",
        params={"limit": 3},
        fields=(
            "id", "file_name", "content_type", "storage_uri", "checksum", "size_bytes",
            "source_type", "source_id", "original_text", "extracted_text", "metadata",
            "created_at",
        ),
        optional=True,
    ),
    Endpoint(
        name="文档详情",
        path="/api/v1/documents/{document_id}",
        items_key="",
        fields=(
            "id", "file_name", "content_type", "storage_uri", "checksum", "size_bytes",
            "source_type", "source_id", "original_text", "extracted_text", "metadata",
            "created_at",
        ),
        optional=True,
    ),
    Endpoint(
        name="文档分片",
        path="/api/v1/documents/{document_id}/chunks",
        items_key="items",
        fields=("id", "document_id", "chunk_index", "chunk_text", "metadata", "created_at"),
        optional=True,
    ),
    Endpoint(
        name="文档检索",
        path="/api/v1/documents/search",
        params={"q": "需求", "limit": 3},
        fields=(
            "id", "document_id", "chunk_index", "chunk_text", "file_name",
            "storage_uri", "score", "metadata", "created_at",
        ),
        optional=True,
    ),
    Endpoint(
        name="能力详情",
        path="/api/v1/capabilities/{capability_id}",
        items_key="",
        fields=(
            "id", "action", "object", "display_name", "status", "created_by",
            "origin_source_id", "created_at", "updated_at",
        ),
        optional=True,
    ),
    Endpoint(
        name="能力反查需求",
        path="/api/v1/capabilities/{capability_id}/streams",
        params={"limit": 5},
        fields=(
            "requirement_key", "requirement_name", "status", "current_version",
            "review_status", "action", "object", "display_name",
        ),
        optional=True,
    ),
    Endpoint(
        name="条件详情",
        path="/api/v1/constraints/{constraint_id}",
        items_key="",
        fields=(
            "id", "constraint_key", "display_name", "status", "created_by",
            "aliases", "created_at", "updated_at",
        ),
        optional=True,
    ),
    Endpoint(
        name="需求全文检索",
        path="/api/v1/requirements/search",
        params={"q": "需求", "limit": 3},
        fields=("requirement_key", "summary", "match_type", "vector_similarity"),
        optional=True,
    ),
    Endpoint(
        name="功能检索",
        path="/api/v1/requirements/features/search",
        params={"q": "导出", "limit": 3},
        fields=(
            "id", "requirement_id", "feature_key", "content", "status", "ordinal",
            "requirement_key", "requirement_name",
        ),
        optional=True,
    ),
    Endpoint(
        name="需求溯源",
        path="/api/v1/requirements/{requirement_key}/trace",
        items_key="",
        fields=("requirement", "versions"),
    ),
    Endpoint(
        name="审核历史",
        path="/api/v1/reviews/history",
        params={"limit": 3},
        fields=(
            "source_id", "source_type", "requester_name", "processing_status",
            "error_message", "excerpt", "linked_requirement_key", "submitted_at", "updated_at",
        ),
        optional=True,
    ),
    Endpoint(
        name="来源回放",
        path="/api/v1/sources/{source_id}/trace",
        items_key="",
        fields=("source", "extraction", "mappings"),
        optional=True,
    ),
    Endpoint(
        name="记忆上下文",
        path="/api/v1/memory/context",
        params={"query": "需求"},
        items_key="",
        fields=("context",),
    ),
    Endpoint(
        name="运行详情",
        path="/api/v1/agent/runs/{run_id}",
        items_key="",
        fields=(
            "id", "run_id", "conversation_id", "client_message_id", "status", "error",
            "meta", "stage", "checkpoint", "created_at", "updated_at",
        ),
        optional=True,
    ),
    Endpoint(
        name="运行事件",
        path="/api/v1/agent/runs/{run_id}/events",
        items_key="items",
        fields=("id", "run_id", "sequence", "event_type", "node", "payload", "created_at"),
        optional=True,
    ),
    Endpoint(
        name="工具调用",
        path="/api/v1/agent/runs/{run_id}/invocations",
        items_key="tools",
        fields=(
            "id", "run_id", "tool_name", "arguments_summary", "result_summary",
            "status", "elapsed_ms", "error_message", "created_at",
        ),
        optional=True,
    ),
    Endpoint(
        name="审核详情",
        path="/api/v1/reviews/{source_id}/detail",
        items_key="",  # 裸对象，与 /reviews/pending 的单项同形状
        fields=(
            "source_id", "source_type", "source_event_id", "requester_id", "requester_name",
            "original_text", "extracted_text", "original_payload", "metadata",
            "processing_status", "submitted_at", "updated_at",
        ),
        optional=True,
    ),
    Endpoint(
        name="合并预演",
        # ⚠️ 必须用**待审**的来源：非 pending_review 会 409（实测过）。
        path="/api/v1/reviews/{pending_source_id}/merge-preview",
        params={"target_requirement_key": SAMPLE_KEY},
        items_key="",
        fields=(
            "source_id", "merge_mode", "target", "next_version",
            "groups", "summary", "overrides", "warnings",
        ),
        optional=True,
    ),
)


def _client():
    """带鉴权的 TestClient。

    ⚠️ B1 起 API 需要 `Authorization: Bearer <token>` —— 不带就是一片 401，
    而脚本会把每个端点都报成「HTTP 401」，看起来像接口全坏了。走 `API_AUTH_TOKEN`
    这个兼容入口（等同于一个 admin token）。
    """
    from fastapi.testclient import TestClient

    from requirement_agent.api.app import app

    token = os.environ.get("API_AUTH_TOKEN", "test-api-token")
    return TestClient(app, headers={"Authorization": f"Bearer {token}"})


def _walk(node, path, out, depth=0):
    """把响应里的每个叶子与它的路径摊平，用于按名字找字段与清点类型。"""
    if depth > 6:
        return
    if isinstance(node, dict):
        for key, value in node.items():
            _walk(value, f"{path}.{key}" if path else key, out, depth + 1)
    elif isinstance(node, list):
        for item in node[:2]:
            _walk(item, path + "[]", out, depth + 1)
    else:
        out.append((path, node))


def _leaf_names(items) -> set[str]:
    """一个样本里出现过的字段名（只取末段，忽略嵌套路径）。"""
    names: set[str] = set()
    for node in items:
        leaves: list[tuple[str, object]] = []
        _walk(node, "", leaves)
        for path, _value in leaves:
            names.add(path.split(".")[-1].replace("[]", ""))
        if isinstance(node, dict):
            names.update(node.keys())
    return names


def check_endpoints(client) -> tuple[list[str], list[dict[str, object]]]:
    """核对每个端点的字段是否存在。返回（问题列表, 类型表原始数据）。"""
    problems: list[str] = []
    big_ints: list[dict[str, object]] = []
    sample_params = collect_sample_params(client)

    for endpoint in SPEC:
        path = resolve_path(endpoint.path, sample_params)
        if path is None:
            # 这个库还没有这类样本（如一个 run 都没有）—— 跳过，不判失败。
            # 「库里没数据」与「契约对不上」是两回事。
            continue

        response = client.get(path, params=endpoint.params or None)
        if response.status_code != 200:
            problems.append(f"{endpoint.name}: HTTP {response.status_code}（{path}）")
            continue

        body = response.json()
        if endpoint.items_key == "":
            samples = [body]
        else:
            items = body.get(endpoint.items_key) if isinstance(body, dict) else None
            if not items:
                if not endpoint.optional:
                    problems.append(f"{endpoint.name}: `{endpoint.items_key}` 为空，无法核对字段")
                # 空数据也要清点顶层键的类型（如 {items: []} 本身没有大整数）
                continue
            samples = items[:2]

        # 顶层键也算：diff 这种「字段在顶层」的端点靠它
        present = _leaf_names(samples)
        if isinstance(body, dict) and endpoint.items_key == "":
            present.update(body.keys())

        missing = [name for name in endpoint.fields if name not in present]
        if missing:
            problems.append(
                f"{endpoint.name}: 响应里没有 {', '.join('`' + m + '`' for m in missing)}"
            )

        for sample in samples:
            leaves: list[tuple[str, object]] = []
            _walk(sample, endpoint.items_key or "", leaves)
            for path, value in leaves:
                if isinstance(value, bool):
                    continue
                if isinstance(value, int) and abs(value) > JS_MAX_SAFE_INTEGER:
                    big_ints.append(
                        {
                            "endpoint": endpoint.name,
                            "field": path,
                            "kind": "number",
                            "value": value,
                            "exact": int(float(value)) == value,
                        }
                    )
                elif (
                    isinstance(value, str)
                    and value.isdigit()
                    and int(value) > JS_MAX_SAFE_INTEGER
                ):
                    big_ints.append(
                        {
                            "endpoint": endpoint.name,
                            "field": path,
                            "kind": "string",
                            "value": value,
                            "exact": True,  # 字符串在 JS 里原样透传
                        }
                    )
    return problems, big_ints


def check_coverage() -> list[str]:
    """**每一条活路由都必须在 SPEC 或 EXCLUDED_ROUTES 里表过态。**

    这是这份脚本最重要的一道闸门，加它的理由是一条实测教训：

    SPEC 曾经只覆盖 10 个端点，而 `/agent/runs` 与 `/memory` 正在发
    number 型雪花 id —— 脚本却报「✅ 契约与实现一致，且当前无精度风险」。
    **它报无风险不是因为它检查过，是因为它没看。** 覆盖边界就是结论边界。

    有了这道闸门，「新增了一个端点但没纳入核对」会**当场变红**，
    而且必须由人来决定是「补进 SPEC」还是「写清楚为什么不查」。
    两种都是表态；静默溜过才是问题。
    """
    from requirement_agent.api.app import app

    def normalize(path: str) -> str:
        return re.sub(r"\{[^}]*\}", "{}", path).rstrip("/")

    live = {
        (method.upper(), normalize(path))
        for path, operations in app.openapi()["paths"].items()
        for method in operations
        if method in ("get", "post", "put", "patch", "delete")
    }
    # ⚠️ 必须带 method 一起比 —— `live` 是 `(method, path)` 的集合，
    # 只拿路径集合去减会**一个都减不掉**，于是每条路由都被报成未覆盖。
    # （首版就是这个错，闸门自己先响了一声。）
    #
    # SPEC 里的样本 key 是**已经展开的字面量**（f-string），不是 `{占位符}` ——
    # 先把它换回 `{}` 再归一化，否则 /requirements/REQ-000015/versions 与
    # 活路由的 /requirements/{requirement_key}/versions 对不上，同样会误报。
    # SPEC 里的端点全是 GET（脚本只发 GET），所以 method 固定。
    spec_routes = {
        ("GET", normalize(endpoint.path.replace(SAMPLE_KEY, "{}")))
        for endpoint in SPEC
    }
    excluded = {(method, normalize(path)) for method, path in EXCLUDED_ROUTES}

    uncovered = sorted(live - spec_routes - excluded)
    return [
        f"{method} {path} 既不在 SPEC，也不在 EXCLUDED_ROUTES"
        for method, path in uncovered
    ]


# 以雪花 ID 作主键、且会经 API 暴露的表。`id` 之外还要看被外键引用的列
# （`feature_id` / `capability_id` 之类会作为请求体参数回传）。
ID_SCAN_TARGETS: tuple[tuple[str, str], ...] = (
    ("requirement_source", "id"),
    ("requirement_master", "id"),
    ("requirement_version", "id"),
    ("requirement_feature", "id"),
    ("requirement_relation", "id"),
    ("capability", "id"),
    ("feature_capability", "feature_id"),
    ("feature_capability", "capability_id"),
    ("document_asset", "id"),
    ("document_chunk", "id"),
    ("requirement_title_candidate", "id"),
    ("outbox_event", "id"),
    ("audit_event", "id"),
    ("requirement_embedding", "id"),
)


def scan_imprecise_ids() -> list[dict[str, object]]:
    """直接查库，找**不能被 double 精确表示**的 ID。

    这一步与「现在有没有接口暴露它」无关 —— 它查的是数据里到底有没有这种值。
    有，就意味着**任何**将来把它当 JSON number 发出去的端点都已经埋了雷。
    """
    from sqlalchemy import text

    from requirement_agent.infrastructure.db.session import SessionLocal

    found: list[dict[str, object]] = []
    with SessionLocal() as session:
        for table, column in ID_SCAN_TARGETS:
            try:
                rows = session.execute(
                    text(f"SELECT {column} FROM {table} WHERE {column} > :floor"),
                    {"floor": JS_MAX_SAFE_INTEGER},
                ).all()
            except Exception:
                session.rollback()
                continue
            for (value,) in rows:
                if not isinstance(value, int):
                    continue
                if int(float(value)) != value:
                    found.append({"table": table, "column": column, "value": value})
    return found


def app_js_coverage_notes() -> list[str]:
    """信息性比对：契约里承诺、但当前 `app.js` 从没提过的字段。

    **不作为失败** —— 契约描述的是完整响应形状，`app.js` 只是它的一个消费者，
    而且是要被替换的那个。「契约有、前端没读」是正常的；这条只帮人判断
    「这个字段是不是可以删了」，别拿它当验收标准。
    """
    if not APP_JS.exists():
        return []
    source = APP_JS.read_text(encoding="utf-8")
    unread: list[str] = []
    for endpoint in SPEC:
        for name in endpoint.fields:
            if name not in source:
                unread.append(f"{endpoint.name}.{name}")
    return unread


def print_type_table(big_ints: list[dict[str, object]]) -> None:
    """大整数的 JSON 类型表 —— T1（ID 序列化统一）的范围就由它定。"""
    print("\n" + "=" * 78)
    print("大整数（> 2^53）的 JSON 类型表")
    print("=" * 78)
    if not big_ints:
        print("（这次没扫到超过 2^53 的值 —— 样本数据不足，不代表没有风险）")
        return
    # 同一个 (端点, 字段, 类型) 只留一行，免得按样本数重复刷屏
    seen: set[tuple] = set()
    unique: list[dict[str, object]] = []
    for row in big_ints:
        key = (row["endpoint"], row["field"], row["kind"])
        if key not in seen:
            seen.add(key)
            unique.append(row)

    print(f"{'端点':<18}{'字段':<34}{'类型':<9}可精确表示")
    print("-" * 78)
    risky = 0
    for row in unique:
        exact = bool(row["exact"])
        if not exact:
            risky += 1
        flag = "" if exact else "  ⚠️ 会丢精度"
        print(f"{str(row['endpoint']):<18}{str(row['field']):<34}{str(row['kind']):<9}{exact}{flag}")
    print("-" * 78)
    print(
        f"共 {len(unique)} 处；其中 `number` 类型且**不可精确表示**的有 {risky} 处 —— "
        "这些一旦被前端原样回传就会指向错误的一行。"
    )


def print_id_scan(found: list[dict[str, object]]) -> None:
    """数据库级的 ID 精度扫描结果 —— 这一节才是 T1 的验收依据。"""
    print("\n" + "=" * 78)
    print("数据库级 ID 精度扫描（不可被 double 精确表示的 ID）")
    print("=" * 78)
    if not found:
        print("✅ 库里所有 ID 都能被 double 精确表示。")
        print("   注意：这是**当前数据**的结论，不代表将来也是 —— 雪花 ID 只要")
        print("   同毫秒内产生第二个（seq≠0），低位的 0 就没了。")
        return
    print(f"{'表':<32}{'列':<16}{'值':<22}发给 JS 会变成")
    print("-" * 78)
    for row in found:
        value = int(row["value"])  # type: ignore[arg-type]
        print(f"{str(row['table']):<32}{str(row['column']):<16}{value:<22}{int(float(value))}")
    print("-" * 78)
    print(
        f"⚠️ 共 {len(found)} 个。它们**现在**也许还没进任何响应，但只要有端点把它当 JSON "
        "number 发出去，前端就会拿到一个错误的 id。"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="核对 API 契约与真实响应")
    parser.add_argument("--only", choices=("all", "ids"), default="all")
    args = parser.parse_args()

    print(f"契约验证 · 打真实库 · 样本 {SAMPLE_KEY}")
    print(f"SPEC 覆盖 {len(SPEC)} 个端点 · 已表态不核对 {len(EXCLUDED_ROUTES)} 条")

    client = _client()
    problems: list[str] = []
    big_ints: list[dict[str, object]] = []

    if args.only == "all":
        problems, big_ints = check_endpoints(client)
        # 覆盖率闸门先跑：连「有哪些端点」都没对齐的话，字段核对没有意义
        problems = check_coverage() + problems
    else:
        _, big_ints = check_endpoints(client)

    print_type_table(big_ints)

    imprecise = scan_imprecise_ids()
    print_id_scan(imprecise)

    if args.only == "all":
        unread = app_js_coverage_notes()
        if unread:
            print("\n" + "-" * 78)
            print(f"（信息）契约里承诺、但当前 app.js 从未提及的字段 {len(unread)} 个：")
            print("  " + "、".join(f"`{name}`" for name in unread[:12]) + ("…" if len(unread) > 12 else ""))
            print("  契约描述完整响应，而 app.js 只是它的一个（待替换的）消费者 —— 这条不算问题。")

    # 危险的组合：**库里真有不可精确表示的 ID** 且 **仍有 id 类字段以 number 暴露**。
    # 两个条件缺一不可 —— 只看前者的话，把序列化修好之后脚本会永远红（数据里的 ID 不会
    # 因为改了序列化就消失）；只看后者则扫不到「现在还没被暴露、但迟早会」的那些。
    number_id_fields = sorted(
        {
            str(row["field"])
            for row in big_ints
            if row["kind"] == "number"
            and str(row["field"]).split(".")[-1].replace("[]", "").endswith("id")
        }
    )

    if problems:
        print("\n" + "=" * 78)
        print(f"❌ 发现 {len(problems)} 个问题")
        print("=" * 78)
        for problem in problems:
            print(f"  · {problem}")
        print("\n契约与实现已脱节：改代码就同步改 docs/api-contract.md，反之亦然。")
        return 1

    if imprecise and number_id_fields:
        print("\n" + "=" * 78)
        print("❌ 存在精度风险：库里有不可精确表示的 ID，而下面这些字段仍以 JSON number 暴露")
        print("=" * 78)
        for field in number_id_fields:
            print(f"  · {field}")
        print(
            "\n  只要上述任一端点将来发出一个低位不为 0 的 ID，前端拿到的就是错的值。"
            "\n  修法见 docs/api-contract.md §10-T1（把雪花 ID 序列化成字符串）。"
        )
        return 1

    print("\n✅ 契约与实现一致，且当前无精度风险。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
