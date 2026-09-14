# 兼容计划（compatibility-plan）

> 目标：在「只调整结构与依赖、不改变业务行为」的约束下，从 `src.{domain,application,...}` 迁移到 `src.requirement_agent.{...}`。
> 核心手段：**旧路径保留 + 新包转发**，逐步切换 import，杜绝一次性大爆炸。

## 兼容策略

### 1. 命名空间骨架（子批次 1，已完成）
- 已创建 `src/requirement_agent/` 及其 10 个子包 `__init__.py`（纯 docstring，**不转发**）。
- 目的：让新命名空间可被 setuptools 发现、可 `import`，为后续迁移提供落点。
- 旧路径 `src.*` **完全不动**。

### 2. 兼容转发（后续子批次逐步启用）
迁移某子包时，采用两种兼容手段，**二选一或并用**：

- **A. 旧模块瘦身为转发**：把 `src/domain/requirement.py` 改为
  `from src.requirement_agent.domain.requirement import *`（+ `__all__`），原 import 方零改动。
- **B. 新包内转发**：在新子包 `__init__.py` 里 `from src.domain.requirement import X`，
  让 `import src.requirement_agent.domain.requirement` 也能用（双向兼容）。

> 判定标准：任一既有 `from src.<pkg>.<mod> import X` 在迁移后仍必须可用。

### 3. `src.common.snowflake` 特殊处理（P0）
- 现状：`src/common/snowflake.py` 未提交，但已被 8 个模块引用
  （`requirement.py:10`、`audit.py:10`、`review.py:15`、`document.py:13`、`memory.py:12`、
  `chat.py:12`、`outbox.py:14`、`pgvector_repository.py:11`）。
- 约束：**任何情况下保留 `src.common.snowflake` 可导入**（`from src.common.snowflake import new_id`）。
- 若 `common/` 并入新包：迁移后同时提供 `src.requirement_agent.<归属>.snowflake` 与
  `src.common.snowflake`（转发或别名），二者等价。
- 若 `common/` 独立：新路径 `src.requirement_agent.common.snowflake` + 旧路径转发。

### 4. 数据库与迁移文件
- `migrations/` 不移动、不改写；`007_snowflake_ids.sql` 留原地。
- 禁止清库/回滚/重写迁移历史（阶段约束）。

### 5. 禁止变更清单（全程）
- Prompt/Skill/LLM 模型/参数：不改。
- API/MCP 路径、请求/响应 Schema：不改。
- 审核、版本、事务、业务状态逻辑：不改。
- 鉴权行为、静态文件服务：不改。
- `.env`：不改。
- 前端调用：不改。

## 迁移顺序（拟）

| 子批次 | 内容 | 兼容动作 |
|---|---|---|
| 1 | 包骨架 | 仅建命名空间（已完成） |
| 2 | 统一 API 入口（main/apps） | ✅ 已完成：`src/requirement_agent/api/app.py`（`create_app()`+`app`）；根 `main.py` 薄包装转发；`apps/api/main.py`、`apps/api/__main__.py` 保留；新增顶层兼容包 `requirement_agent/api/app.py` 转发到物理实现（单一 app 实例，不重复注册路由） |
| 3 | `interfaces/api`、`interfaces/http` → `requirement_agent/api` | 新包承接，旧路由文件转发，API 路径不变（**注意**：`interfaces/api` 在早前清理中已被删除，当前仅 `interfaces/http`）。进度：3.1 Schema（common/requirements/reviews）✅；3.2.1 只读需求查询路由（versions/features/diff/trace）✅；3.2.2 相似需求检索路由（search/features/search）✅；3.2.3 健康检查路由（health/db、health/llm）✅；3.2.4 来源追踪（sources/trace）+ 审计查询（audit/events）✅；3.2.5 审核只读（reviews/pending、reviews/{id}/detail）✅；3.3.2 `POST /agent/run`（run_agent_pipeline → routes/agent.py）✅；3.3.3 `POST /reviews/submit`（submit_review_decision → routes/reviews.py 的 submit_router）✅；3.3.4 conversations 全域（7 条 → routes/conversations.py）✅；3.3.5 剩余全部路由 ✅（rest.py/agent_chat.py 变纯转发壳）；3.3.6 收尾：`_state`→`api/dependencies.py`、剩余 Schema→`api/schemas/`、聚合器→`api/router.py`，**删除 `src/interfaces/`** ✅；3.3.7 其余分层整体迁入 ✅；3.3.8 转 src-layout（导入 `requirement_agent.*`、删 shim、pyproject/pytest 调整、PyCharm 运行配置）✅ **——阶段 1 完成** |
| 4 | `domain/application/infrastructure` 迁移 | 新包承接，旧包转发；snowflake 双向兼容 |
| 5 | `agents/skills/workflows` 迁移 | 新包承接，旧包转发；Prompt/参数/阈值不变 |

## 回滚方式
- 每个子批次改动都可通过「删除新包文件 / 撤销 pyproject 改动 / git 恢复」回滚。
- 因旧路径始终保留，回滚后任何 import 仍指向旧实现，业务行为不变。

## MCP 与内部 Tool 状态

**MCP 已彻底移除**（按用户指示）——不再以 MCP 作为工具调用方式，改为内部可直接调用的方法。

- 已删除：`apps/mcp/`、`src/interfaces/mcp/`（tools/auth）、`src/requirement_agent/mcp/`（骨架）、
  docker-compose 的 `mcp` 服务、settings 的 MCP 协议配置（`mcp_port`/`mcp_auth_token`/
  `mcp_issuer_url`/`mcp_resource_url`/`require_mcp_auth`）、pyproject 的 `mcp` 依赖。
- 新增：`src/requirement_agent/tools/`——原 7 个 MCP 工具转为**普通方法**，工具体**逐字保留**。
- **保留** `settings.mcp_actor_id`（`MCP_ACTOR_ID`）：转换后的 `submit_review_decision` 方法
  仍用它作为 `reviewer_id`（保持审核行为一致）。
- 无核心模块依赖 MCP（主 app 从未引用）。
- 未迁移/删错？可从 git 历史恢复（MCP 文件均曾被 git 跟踪）。
