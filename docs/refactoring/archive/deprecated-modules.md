# 待清理 / 兼容保留项（deprecated-modules）

> 规则：**只登记，不删除**。任何删除需单独确认。本文件随结构重组推进逐条更新。

## 兼容保留 / 待废弃

| 项 | 位置 | 状态 | 说明 |
|---|---|---|---|
| MCP 工具层 | ~~`apps/mcp/`、`src/interfaces/mcp/`~~ | ✅ **已删除** | 按用户指示转为内部方法 `src/requirement_agent/tools/`（7 方法，逐字保留）；MCP 代码 + docker-compose `mcp` 服务 + 协议配置 + pyproject `mcp` 依赖均已移除；可从 git 历史恢复 |
| 内部 Tool 方法集 | ~~`src/requirement_agent/tools/`~~ | ✅ **已删除**（2026-09-16） | 上一条的产物，`df44749` 逐字保留下来后**从未接线**：实测包外零引用、启动不加载、无测试覆盖。删除经用户确认（本轮即「单独确认」）。恢复见文末 §已删除 |
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
| **`src/interfaces/` 整个包** | 阶段1 收尾（commit `180578b`）已删除：全部路由/依赖/Schema/聚合器迁至 `src/requirement_agent/api/`；可由 git 历史恢复 |
| **`src/requirement_agent/tools/` 整个包**（2026-09-16） | 见下方盘点。⚠️ **该路径已被新内容占用**，恢复命令见下方警告；最早的 MCP 形态见 `git show df44749^:src/interfaces/mcp/tools.py` |

> ### ⚠️ 这个路径现在住着**另一个** `tools` 包，恢复不能照旧命令抄
>
> 2026-09-16 在**同一路径** `src/requirement_agent/tools/` 落地了**新的 Agent 工具层**
> （`07edb20`，方案 `docs/方案_Agent工具层.md`）：`base.py` / `registry.py` / 10 个工具模块。
> 与被删掉的那个包（MCP 时代的 `health.py` / `requirements.py` / `reviews.py` / `_deps.py`）
> **只共享路径，没有任何继承关系** —— 新包甚至就是因为它才被写出来的。
>
> 直接跑 `git checkout b8fc783^ -- src/requirement_agent/tools/` 会**用旧包的 `__init__.py`
> 覆盖新包的名录**，于是 10 个新工具模块**不再被导入**、`@register` 不执行、
> 注册表变空 —— `tests/unit/test_tool_registry.py` 会红（**响亮地失败，不是静默**）。
>
> **想看旧包的内容 —— 不要往工作区里 checkout**，用 `git show` 读单个文件：
>
> ```bash
> git show b8fc783^:src/requirement_agent/tools/requirements.py   # 旧包里的某个文件
> git ls-tree -r b8fc783^ --name-only | grep 'tools/'             # 旧包里都有什么
> ```
>
> 真要整包拿回来，先 checkout 到一个**空目录**再比对（`git --work-tree=<空目录> checkout ...`），
> 别覆盖现行工具层。
>
> **它的能力并没有丢**，多数已被新工具层覆盖（见下方对照表）。

### 旧包的 7 个方法 → 新工具层的去向（2026-09-16 核对）

| 旧方法 | 现在 |
|---|---|
| `search_requirements` | ✅ **同名同义**，新工具层有 |
| `submit_requirement` | ✅ **同名同义**（仍是唯一写入口，只进待审） |
| `get_requirement_detail(requirement_id)` | ✅ 换成 `get_requirement(requirement_key)` —— **改按 `requirement_key` 定位**（雪花 id 前端根本拿不到） |
| `get_requirement_versions(requirement_id)` | ✅ 换成 `trace_requirement(requirement_key)` —— **更强**：不只版本号，还带每版的来源链 |
| `submit_review_decision(...)` | ❌ **故意不做** —— 裁决是人的职责，这是新工具层的硬约束（方案 §2.2） |
| `get_master_requirements(limit, status)` | ⚠️ **没有对应**（见下） |
| `health_check` | ❌ 没有对应，也不需要（真实健康检查在 `api/routes/health.py`） |
| `ingest_channel_event`（未进 `__all__`） | ❌ 没有对应 —— 渠道接入本身是挂起项（current-state §二 C1） |

> **`get_master_requirements` 是个真实缺口**：新工具层有 `search_requirements`（按语义找）
> 与 `get_requirement`（按编号取），但**没有「浏览 / 列举需求」**这一类。
> 模型想回答「现在一共有哪些需求」时无从下手。要不要补一个 `list_requirements`
> 是待定项 —— 补的成本很低（转发到 `RequirementService.list_requirements`），
> 但要先想清楚「列举」会不会把整个需求库灌进上下文（可能要强制分页 + 上限）。

### 已删除：`src/requirement_agent/tools/` 盘点（2026-09-16）

**为什么删**：它不是「有几个文件没用」，而是**整个包都是孤儿**。

| 检查 | 结果 |
|---|---|
| 包外引用它的代码（`src/` `tests/` `scripts/` `main.py`） | **0** |
| 启动 API 后加载的 tools 模块（`sys.modules`） | **0**（只有无关的 `pydantic.v1.tools`） |
| 测试覆盖 | 0 |

**为什么它「不像工具」**：定义机制随 MCP 一起删了。原形态（`src/interfaces/mcp/tools.py`）里
`@mcp.tool()` 负责注册名字 + 由类型注解生成 JSON schema，`mcp = FastMCP(...)` 是注册表，
`StaticTokenVerifier` 管鉴权。`df44749` 删 MCP 时这三样全删、函数体逐字留下 ——
于是只剩「一个叫 tools 的包里装着几个普通函数」。`compatibility-plan.md` 当时已写明
**「无核心模块依赖 MCP（主 app 从未引用）」**，即它从诞生起就没有调用方。

| 文件 | 行数 | 内容 |
|---|---|---|
| `__init__.py` | 36 | 聚合导出，无消费者 |
| `_deps.py` | 33 | 单例装配（与 `api/dependencies.py` **重复的一套**），包外 0 引用 |
| `health.py` | 11 | `health_check()` 返回硬编码 `{"status":"ok"}`；真实健康检查在 `api/routes/health.py` |
| `requirements.py` | 123 | 6 个转发函数；其中 `ingest_channel_event` 未进任何 `__all__`，包命名空间导不出来 |
| `reviews.py` | 30 | `submit_review_decision` 与 `api/routes/reviews.py` 的同名 handler 是不同对象 |

**连带清理**：`settings.tool_actor_id`（`TOOL_ACTOR_ID`）唯一消费者是 `tools/reviews.py`，
一并删除；`.env.example` 对应行同步移除。<u>若将来重新对外暴露 MCP 工具面，不要照搬
`_deps.py` 的第二套单例，应复用 `api/dependencies.py`。</u>
