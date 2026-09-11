# 待清理 / 兼容保留项（deprecated-modules）

> 规则：**只登记，不删除**。任何删除需单独确认。本文件随结构重组推进逐条更新。

## 兼容保留 / 待废弃

| 项 | 位置 | 状态 | 说明 |
|---|---|---|---|
| MCP 工具层 | ~~`apps/mcp/`、`src/interfaces/mcp/`~~ | ✅ **已删除** | 按用户指示转为内部方法 `src/requirement_agent/tools/`（7 方法，逐字保留）；MCP 代码 + docker-compose `mcp` 服务 + 协议配置 + pyproject `mcp` 依赖均已移除；可从 git 历史恢复 |
| 顶层兼容包 | `requirement_agent/`（根，转发到 `src.requirement_agent`） | 兼容保留 | 提供 `requirement_agent.*` 目标导入路径；迁移完成后可评估回收 |
| `apps/api/` | 冗余入口壳（`from main import app`） | 兼容保留 | 与根 `main.py` 等价；是否合并待人工确认 |

## 部分迁移中（新旧混合）

| 项 | 现状 | 后续 |
|---|---|---|
| `src/interfaces/http/agent_chat.py` | `/agent/run`（run_agent_pipeline）已于 3.3.2 迁出至 `api/routes/agent.py`，本文件转发复用；SSE 三个流式端点（chat/stream、chat/stream-with-files）**仍在本文件** | 建议整体迁至 `api/routes/agent_chat.py`（P3，需先设计服务边界） |
| `src/interfaces/http/rest.py` | 只读路由已迁出多组（3.1–3.2.5），剩余写路由与 documents/conversations/memory 只读组 | 逐步迁移；未使用导入已登记待清理 |

> 更正：`run_agent_pipeline` 原位于 `agent_chat.py`（**非** rest.py）；任务描述中的
> `from src.interfaces.http.rest import run_agent_pipeline` 不成立，真实兼容路径为
> `src.interfaces.http.agent_chat.run_agent_pipeline`。

## 待确认归属（暂不迁移）

| 项 | 位置 | 说明 |
|---|---|---|
| `AgentRunRequest` / `AgentChatRequest` | `src/interfaces/http/schemas.py` | 目标 `schemas/` 无 agent_chat.py，归属待确认 |
| `ConversationCreateRequest` / `ConversationUpdateRequest` / `ConversationMessageCreateRequest` | `src/interfaces/http/schemas.py` | 目标 `schemas/` 无 conversations.py，归属待确认 |
| `HealthResponse` | `src/requirement_agent/api/schemas/common.py`（已迁移） | 当前**无任何引用**（未出现在 OpenAPI），保留待确认是否废弃 |
| `feishu_client.py` | `src/infrastructure/channels/feishu_client.py` | 未接线 stub（飞书渠道待接入） |

## 已发现但**不处理**的既有问题（仅登记）

| 项 | 说明 | 影响 |
|---|---|---|
| `/health` 重复注册 | `src/interfaces/http/rest.py`（router 版）与 `src/requirement_agent/api/app.py`（app 版）各注册一个 `/health`，operation_id 均为 `healthcheck_health_get`（OpenAPI 有重复 warning） | 重构前即存在；本阶段不改（避免改变行为） |
| OpenAPI tags 重复 | 所有 rest 路由的 OpenAPI `tags` 为 `['requirements','requirements']`（`rest.py` 与 `routes.py` 聚合器各带一次 `tags=["requirements"]`） | 重构前即存在且全站一致；本阶段不改 |
| rest.py 未使用导入 | `version_repo` / `feature_repo`（3.2.1）、`retrieval_service`（3.2.2）、`check_database_connection` / `LLMProvider`（3.2.3）、`audit_repo`（3.2.4）、`source_repo`（3.2.5）、`review_service` / `ReviewSubmitRequest`（3.3.3）、`uuid4` / `chat_repo` / `summarize_text` / `memory_extractor` / `memory_repo` / `Conversation*Request`（3.3.4 迁出后）仅剩导入（`src/interfaces/http/rest.py`） | 保留不删（避免扩大改动面），后续清理 |

## 已删除（历史，非本轮）

| 项 | 说明 |
|---|---|
| `src/interfaces/api/`（routes/schemas）、`src/interfaces/http/auth.py` | 早前「死代码清理」已删除并提交（`5c5386f`）；当前无残留引用 |
