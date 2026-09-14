# 路径映射（path-mapping）

> 维护：结构重组过程中，新旧路径的映射关系（**不执行**，仅登记；迁移方式仅限「原样迁移/拆分后迁移/保留兼容转发/暂不迁移/需要人工确认」）。
> 现状依据：`docs/refactoring/phase-0-baseline-report.md`。

## 原则

- 以**实际职责**为准，不机械移动。
- 迁移期间：旧路径保持可用（兼容转发），新路径逐步承接。
- 删除任何文件前必须先有迁移后的等价物确认；两个职责相近时**保留 + 兼容转发**，记入待清理。

## 顶层映射

| 目标子包 | 当前路径（旧） | 当前职责 | 迁移方式 | 风险 | 状态 |
|---|---|---|---|---|---|
| `requirement_agent/api/` | `src/interfaces/http/` | HTTP 路由/Schema/状态 | 拆分后迁移 | 高 | ⏳ 子批次3 |
| `requirement_agent/api/` | 根 `main.py` | FastAPI 入口 + lifespan | 保留兼容转发 | 高 | ✅ 子批次2 完成 |
| `requirement_agent/tools/` | `src/interfaces/mcp/tools.py` | MCP 工具 → 内部方法 | ✅ 已转换（3.3.5） | 低 | 完成 |
| `requirement_agent/workers/` | `src/infrastructure/worker/`、`apps/worker/` | 消费循环 / tasks / outbox | 拆分后迁移 | 中 | ⏳ 子批次2/4 |
| `requirement_agent/domain/` | `src/domain/` | 领域 dataclass | 原样迁移 | 低 | ⏳ 子批次4 |
| `requirement_agent/application/` | `src/application/` | 应用服务/事务编排 | 原样迁移 | 中 | ⏳ 子批次4 |
| `requirement_agent/agents/` | `src/agents/` | extract/analyze/retrieval/risk Agent | 原样迁移 | 中 | ⏳ 子批次5 |
| `requirement_agent/skills/` | `src/skills/` | Skill + prompts | 原样迁移 | 中 | ⏳ 子批次5 |
| `requirement_agent/workflows/` | `src/graph/` | LangGraph state + 节点 + 图 | 拆分后迁移 | 中 | ⏳ 子批次5 |
| `requirement_agent/infrastructure/` | `src/infrastructure/` | db/llm/embedding/vector/parser/storage/channels | 原样迁移 | 高 | ⏳ 子批次4 |
| `requirement_agent/config/` | `src/config/` | settings | 原样迁移 | 高 | ⏳ 子批次4 |
| `requirement_agent/common/` | `src/common/` | time / snowflake | 需要人工确认（目标结构未列） | 高 | ⚠️ 待确认 |
| `apps/api/` | （冗余壳） | 复用根 main | 需要人工确认 | 低 | ⚠️ 待确认（子批次2保留为薄壳） |
| `apps/mcp/` | （宿主） | uvicorn 宿主 | 保留兼容转发 | 低 | ⏳ 子批次2 |
| `scripts/` | 保留 | 运维脚本 | 原样迁移 | 低 | 保留 |
| 根 `需求管理Agent.yml` 等 | 根 | 待确认 | 需要人工确认 | 低 | ⚠️ 待确认 |

## 未跟踪/关键文件映射（第0阶段发现，P0）

| 文件 | import/迁移依赖 | 是否需迁移新包 | 说明 |
|---|---|---|---|
| `src/common/snowflake.py` | `src.common.snowflake` 被 8 处引用（requirement/audit/review/document/memory/chat/outbox/pgvector） | 需迁移（随 `common` 归属确认；若 `common` 并入 `infrastructure` 则路径为 `infrastructure.common.snowflake`） | 迁移时必须**保留 `src.common.snowflake` 兼容转发**，见 compatibility-plan |
| `migrations/007_snowflake_ids.sql` | 无 import（纯 SQL），依赖 001/005（IDENTITY 表） | 否，留在 `migrations/` | 数据库迁移与包无关，不动 |
| `tests/unit/test_snowflake.py` | `from src.common.snowflake import ...` | 迁移时随测试移入新包测试目录，或保留原 import | 保留旧 import 兼容 |

## 阶段状态

- 子批次1：已建 `src/requirement_agent/` 及 10 个子包空 `__init__.py`；**未迁移任何代码**。
- 子批次2：已建统一入口 `src/requirement_agent/api/app.py`（`create_app()` + `app`），
  `main.py` 改为薄包装转发；顶层兼容包 `requirement_agent/`（`api.app` 转发到物理实现，
  单一 app 实例）。`apps/api/main.py`、`apps/api/__main__.py` 保留。**未迁移业务代码**。
- 子批次3.1：API Schema 迁移——`HealthResponse` → `api/schemas/common.py`；
  `RequirementSubmitRequest/Response` → `api/schemas/requirements.py`；
  `ReviewSubmitRequest` → `api/schemas/reviews.py`。旧 `src/interfaces/http/schemas.py`
  保留兼容转发（同一类对象）。未迁移：Agent*/Conversation* 类（归属待确认，见 deprecated-modules.md）。
- 子批次3.2.1：路由迁移（只读需求查询组）——
  `src/interfaces/http/rest.py` 的 4 个只读路由
  （`/requirements/{key}/versions|features|diff|trace`）迁至
  `src/requirement_agent/api/routes/requirements.py`；旧 rest.py 用 `include_router`
  在原位置复用（同一实现、顺序不变），并转发 4 个函数名到旧路径。
- 子批次3.2.2：路由迁移（相似需求检索组）——
  `src/interfaces/http/rest.py` 的 `GET /api/v1/requirements/search`、
  `GET /api/v1/requirements/features/search` 迁至 `api/routes/requirements.py` 的
  独立 `search_router`（因原位置早于 3.2.1 的查询组，需独立 include 以保顺序）；
  旧 rest.py 在原位置 include + 转发函数名。
- 子批次3.2.3：路由迁移（健康检查组）——`src/interfaces/http/rest.py` 的
  `GET /api/v1/health/db`、`GET /api/v1/health/llm` 迁至 `api/routes/health.py`；
  旧 rest.py 在原位置 include + 转发函数名。
- 子批次3.2.4：路由迁移（来源追踪 + 审计查询）——`GET /api/v1/sources/{source_id}/trace`
  → `api/routes/sources.py`；`GET /api/v1/audit/events` → `api/routes/audit.py`。
  二者不连续（中间夹 3.2.1 查询组 include），故各用独立子 router 分别插回原位。
- 子批次3.2.5：路由迁移（审核工作台只读查询）——`GET /api/v1/reviews/pending`、
  `GET /api/v1/reviews/{source_id}/detail` 迁至 `api/routes/reviews.py`；
  旧 rest.py 在原位置 include + 转发函数名。`POST /api/v1/reviews/submit`（写库）保留旧文件。
- 未迁移（暂缓）：documents / conversations / memory 只读组、以及所有写库路由
  （submit/ingest/reviews-submit/memory 写）与 Agent Chat/SSE。
- 子批次3.3.2：`POST /api/v1/agent/run`（`run_agent_pipeline`）迁至 `api/routes/agent.py`。
  **更正**：该函数实际位于 `src/interfaces/http/agent_chat.py`（非 rest.py）；
  旧路径兼容为 `src.interfaces.http.agent_chat.run_agent_pipeline`；同模块
  `chat_with_agent` 仍复用它（经 import 解析）。
- 子批次3.3.3：`POST /api/v1/reviews/submit`（`submit_review_decision`）迁至
  `api/routes/reviews.py` 的独立 `submit_router`（因原位置在末尾、与只读组不同，
  需独立 router 以保顺序）。强事务语义仍由 `ReviewService.submit_decision` 持有。
- 子批次3.3.4：**conversations 全域**（7 条，连续）迁至 `api/routes/conversations.py`
  （list/create/messages×2/update/delete/finalize）。因只读与写在原文件交错、无法单拆只读组，
  整块作为一个 router 迁移以保顺序。逐字迁移，含 finalize（LLM 调用未改）。
- 子批次3.3.5（**一键全迁移**）：剩余全部路由迁出——
  `system.py`（/、/health）、`requirements_write.py`（列表/submit/ingest）、
  `documents.py`（5 条）、`memory.py`（4 条）、`agent_chat.py`（agent 5 条 + SSE 辅助，整文件 `git mv`）。
  至此 **rest.py 与 agent_chat.py 均为纯转发壳**，全部 42 条路由实现在 `api/routes/`。
- 子批次3.3.6（**阶段1 收尾**）：`_state.py`→`api/dependencies.py`；剩余 5 个 Schema→
  `api/schemas/{agent,conversations}.py`；路由聚合器→`api/router.py`（保留两级聚合）；
  **删除整个 `src/interfaces/`**（http 包 + 空包）。至此新包自洽，旧路径不再被引用。
- 阶段 1（结构重组）**完成**：全部 42 路由 + 依赖 + Schema + 聚合器均在 `src/requirement_agent/` 下。
- 子批次3.3.7（**其余分层全迁移**）：`domain / common / config / infrastructure / application /
  agents / skills` 整体 `git mv` 入新包；`graph → workflows`（重命名）；全局改写 64 个文件 import。
  `src/` 顶层仅剩 `requirement_agent/`。**阶段 1 至此全部完成**。
- 子批次3.3.8（**src-layout**）：导入统一为 `requirement_agent.*`（非 `src.requirement_agent.*`）；
  删顶层 shim；pyproject 改 src-layout；pytest `pythonpath=[src, "."]`。PyCharm `.iml` 的
  `src` 源码根与此一致，IDE 无解析错误。