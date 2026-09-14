# 第 0 阶段：现状冻结与基线验证报告

> 生成日期：2026-09-11
> 工作区：`/home/wangmengyang/Software/Requirement_agent/Requirement_agent`
> 事实源：**当前工作区实际文件**（唯一事实源；旧 README/压缩包/审计报告仅作参考，不作为依据）
> 证据标注：`代码事实`（直接读到）/ `合理推断` / `运行时待验证`
> 本阶段：只读 + 测试 + 生成报告，**未修改/删除/移动任何代码与数据**

---

## 1. 当前状态记录

```bash
pwd  : /home/wangmengyang/Software/Requirement_agent/Requirement_agent
git repo: true（分支 master）
```

### Git 状态（`git status --short`）
```
?? migrations/007_snowflake_ids.sql
?? src/common/snowflake.py
?? tests/unit/test_snowflake.py
```
工作区无已修改（M/D/A）文件，仅 3 个未跟踪新文件。

### 最近提交（`git log -5`）
| 提交 | 说明 |
|---|---|
| `5c5386f` | 雪花改造 + 死代码清理 + 注释 |
| `2b43340` | 修复文档向量检索：SQL 参数绑定与路由遮蔽 |
| `74a606a` | 优化 embedding 模型与向量数据库维度升级 |
| `e533ff0` | 优化 embedding 模型与向量数据库维度升级 |
| `66d9177` | 重构：拆分超大 repositories 与 routes，清理死代码 |

### ⚠️ 关键状态风险（P0，`代码事实`）
代码中已存在 `from src.common.snowflake import new_id`（`requirement.py:10`、`audit.py:10`、`review.py:15`、`document.py:13`、`memory.py:12`、`chat.py:12`、`outbox.py:14`、`pgvector_repository.py:11`），且迁移链依赖 `007_snowflake_ids.sql`，但**这三个文件尚未提交**（untracked）。**全新 clone 该 HEAD 提交会 `ModuleNotFoundError: src.common.snowflake`，且迁移链缺 007。** 需在后续阶段尽快提交。

---

## 2. 完整目录树（基于实际扫描）

```
Requirement_agent/
├── main.py                       # FastAPI 正式入口（根入口）
├── pyproject.toml                # 依赖与打包（>=3.12）
├── README.md                     # 文档（可能过时）
├── uv.lock                       # uv 锁文件
├── test_main.http                # HTTP 测试片段
├── 需求管理agent问题清单.md       # 根目录文档（未归类）
├── 需求管理Agent.yml             # 根目录大文件（87KB，YAML，待确认用途）
├── .env                          # 配置（gitignore，含密钥）
├── .gitignore
├── .idea/                        # IDE 文件（部分被 git 跟踪，见 §3）
├── apps/                         # 应用入口层
│   ├── api/{__init__,__main__,main}.py    # API 入口（薄壳，复用根 main）
│   ├── mcp/{__init__,__main__,server}.py  # MCP 入口
│   └── worker/{__init__,__main__,tasks}.py# Worker 入口（独立 FastAPI :8200）
├── src/
│   ├── agents/{extract,analyze,retrieval,risk}_agent.py
│   ├── application/{requirement,retrieval,review}_service.py
│   ├── application/{decision_rules,memory_service}.py
│   ├── common/{time,snowflake}.py          # snowflake.py 未跟踪！
│   ├── config/settings.py
│   ├── domain/requirement.py
│   ├── graph/{agents_nodes,commit_nodes,canonical,graphs,state}.py   # LangGraph
│   ├── infrastructure/
│   │   ├── channels/feishu_client.py       # stub，未接线
│   │   ├── db/{session.py, repositories/{audit,chat,document,memory,requirement,review}.py}
│   │   ├── embedding/embedding_service.py
│   │   ├── llm/openai_provider.py
│   │   ├── parser/document_parser.py
│   │   ├── storage/object_store.py
│   │   ├── vector/pgvector_repository.py
│   │   └── worker/{consumer,outbox,tasks}.py
│   ├── interfaces/http/{_state,agent_chat,rest,routes,schemas}.py
│   ├── interfaces/mcp/{auth,tools}.py
│   └── skills/{base,extract,analyze,risk}_skill.py, prompts.py
├── migrations/
│   ├── 001_init_business_schema.sql         # 16 表 + 序列 + 触发器
│   ├── 002_outbox_retry_dead_letter.sql     # 幂等
│   ├── 003_conversation_memory.sql
│   ├── 004_document_asset_and_chunks.sql
│   ├── 005_requirement_feature_versioning.sql
│   ├── 006_embedding_dimension_4096.sql     # ⚠️ 一次性破坏性（含 DELETE）
│   ├── 007_snowflake_ids.sql                # ⚠️ 未跟踪
│   └── README.md
├── static/{index.html, css/styles.css, js/app.js}   # 前端（卡片工作台）
├── deploy/docker-compose.yml               # postgres/minio/migrate/api/mcp；无 worker
├── scripts/{__init__,reindex_embeddings}.py
├── storage/uploads/                        # 本地存储目录（当前空）
├── tests/
│   ├── unit/  （13 个测试文件，见 §9）
│   ├── integration/test_api_requirements.py
│   └── e2e/    （空，仅 __init__）
└── docx/                                   # 设计文档（gitignore）
    ├── IMPLEMENTATION_PLAN.md / 任务清单.md
    ├── 企业需求治理Agent_项目需求说明书.md
    ├── 长期记忆与多对话_架构设计.md
    ├── 需求版本管理与溯源_实施方案.md
    ├── 需求覆盖差距分析与企业级路线图.md
    └── LLM_Agent_Prompt_Skill_审计报告_2026-09-11.md   # 本轮审计存档
```

### 文件类型标记
| 类型 | 位置 |
|---|---|
| Python 源码 | `src/`、`apps/`、`scripts/`、`main.py` |
| 配置文件 | `.env`、`pyproject.toml`、`deploy/docker-compose.yml` |
| 数据库迁移 | `migrations/*.sql`（7 个） |
| Agent 文件 | `src/agents/*.py`（4 个）+ `src/graph/*_nodes.py` |
| Skill / Prompt | `src/skills/*.py`（5 个） |
| API 入口 | `main.py`、`apps/api/main.py` |
| MCP 入口 | `apps/mcp/server.py` |
| Worker 入口 | `apps/worker/tasks.py` |
| 前端 | `static/index.html`、`static/js/app.js`、`static/css/styles.css` |
| 测试 | `tests/unit/`、`tests/integration/` |
| 最近新增/修改 | `src/common/snowflake.py`、`migrations/007`、`tests/unit/test_snowflake.py`（未跟踪） |
| 未跟踪文件 | 上述 3 个 |
| 敏感信息文件 | `.env`（含 API Key/DB 口令，已 gitignore，`代码事实`） |
| 缓存/构建/IDE | `__pycache__/`、`.pytest_cache/`、`requirement_agent.egg-info/`、`.venv/`、`.idea/` |

---

## 3. 新增、未跟踪与敏感文件

| 文件 | 状态 | 说明 |
|---|---|---|
| `src/common/snowflake.py` | 未跟踪 | 雪花 id 生成器；代码已引用 → **必须提交，否则 clone 即挂** |
| `migrations/007_snowflake_ids.sql` | 未跟踪 | 7 张表 IDENTITY→BY DEFAULT；迁移链依赖 |
| `tests/unit/test_snowflake.py` | 未跟踪 | 雪花单测 |
| `docx/LLM_Agent_Prompt_Skill_审计报告_2026-09-11.md` | gitignore(docx/) | 本轮审计报告 |
| `.env` | gitignore | 含真实密钥 → 仅本地，不进版本库 ✅ |
| `.idea/*.xml`、`.idea/requirement-agent.iml` | **被 git 跟踪** | IDE 文件入版本库（P3，可后续清理） |
| `storage/uploads/` | gitignore(storage/) | 本地 MinIO 兼容上传目录，当前空 |
| `需求管理Agent.yml`（87KB） | 已跟踪 | 根目录大 YAML，用途待确认（可能为 Dify/编排蓝图） |
| `需求管理agent问题清单.md`、`test_main.http` | 已跟踪 | 根目录文档/HTTP 片段，待确认是否保留 |

---

## 4. 启动入口

| 入口 | 文件 | 启动命令 | 端口 | 状态 |
|---|---|---|---|---|
| **FastAPI 正式入口（推荐）** | `main.py` | `uvicorn main:app --host 0.0.0.0 --port 8888` | 8888 | docker-compose `api` 服务即此 ✅ |
| MCP 入口 | `apps/mcp/server.py` | `uvicorn apps.mcp.server:app --port ${MCP_PORT:-8000}` | 8000 | docker-compose `mcp` 服务 ✅ |
| Worker 入口 | `apps/worker/tasks.py` | `uvicorn apps.worker.tasks:app --port 8200` | 8200 | **未部署**：docker-compose 无 worker 服务（`代码事实`） |
| API 冗余入口 | `apps/api/main.py` | `uvicorn apps.api.main:app --port 8888` | 8888 | 薄壳，仅 `from main import app` 复用根入口；测试引用它 |

**多入口情况**：存在 4 个入口文件。根 `main.py` 是唯一"真实"HTTP 入口（带 lifespan 消费循环）；`apps/api/main.py` 是冗余壳（与根入口等价，测试 `tests/*` 引用 `apps.api.main`）。`代码事实`。

---

## 5. 架构分层（实际职责）

| 层 | 目录 | 实际职责 | 直接访问 DB | 调用 LLM |
|---|---|---|---|---|
| 领域 | `src/domain/` | 纯 dataclass（RequirementSource/Master/Version/Review/AuditEvent） | 否 | 否 |
| 应用服务 | `src/application/` | 编排（RequirementService/ReviewService/RetrievalService/MemoryService/decision_rules） | 通过 repository | 经 Agent/Skill |
| Agent | `src/agents/` | extract/retrieve/analyze/risk 入口 + 启发式 fallback | 否 | 是（经 Skill） |
| Skill | `src/skills/` | 固定 Prompt + JSON 解析 + 回退 | 否 | 是 |
| Workflow | `src/graph/` | LangGraph 两个子图 + 节点 | 决策图节点写库 | 分析图节点经 Agent |
| 基础设施 | `src/infrastructure/` | DB 会话/仓库、LLM Provider、Embedding、Parser、ObjectStore、pgvector、outbox/worker/consumer | 是（repositories） | 是（openai_provider） |
| 接口 | `src/interfaces/http` + `src/interfaces/mcp` | HTTP 路由 + MCP 工具 | 经 service | — |
| 应用入口 | `apps/` | uvicorn 宿主 | — | — |

**关键结论**：
- **直接访问 DB 的模块**：`src/infrastructure/db/repositories/*`（7 个仓库）、`src/infrastructure/vector/pgvector_repository.py`、`src/infrastructure/worker/outbox.py`（`代码事实`）。
- **事务边界**：`ReviewService.submit_decision`（`review_service.py:76-104`）持有 session 并 commit/rollback；`RequirementService.submit_requirement` 部分自建 session。事务**不跨仓库**统一管理，各仓库 `save` 在未传 session 时自建并提交（`代码事实`）。
- **Agent 编排**：`src/graph/graphs.py`（分析子图 + 决策子图）+ `_state.py` 共享单例。
- **跨层调用**：`src/interfaces/http/_state.py` 集中实例化 service/repo/agent 单例，供各 router 取用，**无循环 import**（`代码事实`，模块 docstring 注明设计意图）。
- **Agent 绕过 Application Service？** 否。写库仅发生在决策图节点（`commit_nodes.py`），由 `ReviewService` 触发；MCP 工具声明"绝不直接写库"（`tools.py` 模块 docstring）；Agent/Skill 为纯计算（`代码事实`）。
- **重复职责**：`apps/api/main.py` 与根 `main.py` 重复；`scripts/reindex_embeddings.py` 与 `EmbeddingTask` 逻辑重叠（合理推断，可接受为运维工具）。
- **`interfaces/api/` 已被删除**（前阶段清理，当前树中不存在）。

---

## 6. API / MCP / Worker 清单

### HTTP 路由（`src/interfaces/http/rest.py` + `agent_chat.py`，经 `routes.py` 聚合，根 `main.py` 挂载）
| 前缀 | 端点 | 文件 |
|---|---|---|
| `GET /` `/health` `/api/v1/health/db` `/api/v1/health/llm` | 健康检查 | rest.py |
| `/api/v1/requirements` | submit / ingest / search / features/search / list | rest.py |
| `/api/v1/requirements/{key}` | versions / features / diff / trace | rest.py |
| `/api/v1/documents` | list / {id} / {id}/chunks / {id}/reindex / search | rest.py |
| `/api/v1/conversations` | CRUD / messages / finalize | rest.py |
| `/api/v1/memory` | list / upsert / delete / context | rest.py |
| `/api/v1/reviews` | pending / {id}/detail / submit | rest.py |
| `/api/v1/sources/{id}/trace`、`/api/v1/audit/events` | 溯源/审计 | rest.py |
| `/api/v1/agent/*` | chat / chat/stream / chat/stream-with-files / run / runs/{id} | agent_chat.py（SSE） |

### MCP 工具（`src/interfaces/mcp/tools.py`，`apps/mcp/server.py` 挂载）
`health_check` / `submit_requirement` / `search_requirements` / `submit_review_decision` / `get_requirement_detail` / `get_requirement_versions` / `get_master_requirements`

**路由与工具是否重复**：`submit_requirement` / `search_requirements` / `submit_review_decision` 与 HTTP 的 submit/search/reviews 语义重复，但一个走 HTTP、一个走 MCP（同一 `RequirementService`/`ReviewService`），非代码重复（`代码事实`）。

**Schema 位置**：`src/interfaces/http/schemas.py`（HTTP）+ `src/interfaces/mcp/tools.py`（工具签名）。
**鉴权实现**：`src/interfaces/mcp/auth.py`（`StaticTokenVerifier`，MCP 侧生效，`tools.py:27` 使用）；**HTTP 侧无鉴权**（`代码事实`：rest.py/agent_chat.py 无 `Depends`/鉴权中间件；此前删除的 `http/auth.py` 为死代码）。
**未使用的鉴权代码**：无（`http/auth.py` 已删；MCP auth 在用）。

### Worker（`apps/worker/tasks.py`，独立 FastAPI :8200）
`/health`、`POST /tasks/embedding/process`、`POST /tasks/document-chunk/process`、`GET /tasks/dead-letter`。
另有 `src/infrastructure/worker/consumer.py`：**API 进程内的后台消费循环**（lifespan 启动，默认每 5s 轮询，可配），是 embedding 实际消费方。

---

## 7. Agent / Skill / Prompt / Workflow 清单

### Agent（`src/agents/`）
| Agent | 文件 | 模型 | 写库 | Fallback |
|---|---|---|---|---|
| ExtractAgent | extract_agent.py | DeepSeek（经 ExtractSkill） | 否 | `_fallback_extract`（启发式） |
| RetrievalAgent | retrieval_agent.py | 无（检索服务） | 否 | 无 |
| AnalyzeAgent | analyze_agent.py | DeepSeek | 否 | `_heuristic_analyze` |
| RiskAgent | risk_agent.py | DeepSeek | 否 | `_heuristic_assess` |

### Skill（`src/skills/`）
`BaseSkill`（JSON 提取）、`ExtractSkill`、`AnalyzeSkill`、`RiskSkill`、`prompts.py`（Prompt 常量，**实际未被 Skill 引用**，Skill 各自内联 prompt——`代码事实`，漂移风险）。
**动态 Skill / 版本 / 回滚**：均不支持（普通 Python 类，非插件系统，`代码事实`）。

### Prompt
`EXTRACT/ANALYZE/RISK_SYSTEM_PROMPT` + 用户模板（`prompts.py`），加上 `agent_chat.py:48` `NARRATIVE_SYSTEM_PROMPT`、`memory_service.py:77` `MemoryExtractor.SYSTEM_PROMPT`、`_state.py:78` 摘要 prompt。均为**硬编码字符串**，无版本/回滚。

### Workflow（LangGraph）
- `RequirementState`（`src/graph/state.py:9`）：source_id/source_text/source_type/requester_name/standardized_text/segments/normalized_fields/extracted/candidates/analysis/risk/decision/next_action/review/outcome/ctx/errors（唯一 reducer 是 `errors: Annotated[list, add]`）。
- **分析子图**（`graphs.py:25-43`）：`extract → retrieve → analyze → risk → decide → END`。
- **决策子图**（`graphs.py:46-63`）：`record → route_decision(条件) → commit|reject → END`。
- 节点在 `agents_nodes.py`（纯计算）与 `commit_nodes.py`（写库）。
- **无 checkpointer、无中断恢复、无 recursion_limit**（`代码事实`）。
- 模型：分析图 3 个节点用 DeepSeek；决策图无 LLM。

---

## 8. LLM / Embedding / 检索 / 分片参数（现状，未修改）

> 密钥一律不输出，仅标记来源。

| 项 | 值 | 配置来源 | 位置 |
|---|---|---|---|
| Chat Provider | `deepseek` | `.env LLM_PROVIDER`（默认 `settings.py:35`） | settings |
| Chat 模型 | `deepseek-v4-flash` | `.env DEEPSEEK_MODEL`（默认 `deepseek-chat`，`settings.py:41`） | settings |
| Chat Base URL | `https://api.deepseek.com` | `.env DEEPSEEK_BASE_URL` | settings |
| Chat API Key | 已配置（来自环境变量） | `.env DEEPSEEK_API_KEY` | settings |
| Chat timeout | `30s`（generate）/ `connect=10,read=120,write=30,pool=10`（stream） | 代码硬编码 `openai_provider.py:60,90` | provider |
| temperature / top_p / max_tokens | **未设置**（走 API 默认） | — | provider |
| retry | **无** | — | provider |
| fallback | Skill 层启发式回退（无备用模型） | `agents/*_agent.py` | agents |
| Embedding Provider | oneapi | `.env EMBEDDING_BASE_URL=https://oneapi.pateo.com.cn/v1` | settings |
| Embedding 模型 | `Doubao-embedding` | `.env EMBEDDING_MODEL` | settings |
| Embedding 维度 | `4096` | `.env EMBEDDING_DIMENSION` | settings/DB |
| Embedding timeout | `30s` | `openai_provider.py:132` | provider |
| Embedding 批量/重试 | 无 | — | provider |
| 向量索引 | **无**（4096>2000 建不了 HNSW/IVFFlat，精确扫描） | `006` 迁移 | DB |
| 相似度阈值 | dup≥0.7 / related≥0.45（`analyze_agent.py:89-90`） | 代码硬编码 | agents |
| 人工审核阈值 | duplicate\|conflict\|任一high\|related+≥2中风险（`decision_rules.py:12-26`） | 代码硬编码 | application |
| Top-K | retrieve=5；HTTP search=20；文档 search=5 | `agents_nodes.py:31`、`rest.py:235`、`document.py:144` | 多处 |
| 文档 chunk_size / overlap | **600 / 120**（`tasks.py:75`、`rest.py` 传入；`add_chunks` 默认 600/80 不一致） | 代码硬编码 | document/tasks |
| 需求分片 | **不分片**（整条→单向量） | — | pgvector_repository |
| 关键词+向量融合 | 合并排序，向量分更高时覆盖关键词（`retrieval_service.py:95-121`） | 代码 | retrieval |

---

## 9. 测试结果（基线）

### 已执行（本阶段）
| 检查 | 结果 |
|---|---|
| `python -m compileall src apps main.py scripts tests` | ✅ 通过（无语法错误） |
| 入口导入检查（main / apps.mcp.server / apps.worker.tasks / apps.api.main） | ✅ 4 个全部导入成功 |
| `tests/unit/` 全量单测 | ✅ **13 文件、42 passed、2 skipped**（见下） |
| `tests/integration/test_api_requirements.py` | ⏭️ **未运行**（该测试会向真实 DB 写入 requirement_source，违反"第 0 阶段禁止 DB 写入"约束；此前全量运行曾通过） |

### 单测明细（本阶段实际运行）
| 测试文件 | 结果 |
|---|---|
| test_snowflake / test_outbox_consumer / test_decision_rules / test_embedding_task / test_document_chunk_task / test_extract_agent / test_analyze_skill_consistency / test_retrieval_filters / test_requirement_graph / test_memory_extractor / test_basic / test_review_service / test_retrieval_service | 全部通过 |
| test_document_parser | 2 skipped（依赖可选库 pypdf/docx/PIL+tesseract 缺失时跳过） |

> 注：`test_retrieval_service.py` 含一条连真实 DB 的只读查询（`RetrievalService().search`），本阶段**只读运行通过**，未写入。

### 测试缺口
- `tests/e2e/` 为空（仅 `__init__.py`）——**无 E2E 测试**（`代码事实`）。
- 集成测试仅 1 个（提交冒烟）。
- **外部依赖**：部分测试需本地 PostgreSQL（`test_retrieval_service`、集成测试）；`/ingest` 与文档分块需 MinIO（`storage/` + `minio`）；真实分析需 DeepSeek/oneapi 可达。运行时待验证项需结合部署环境。

---

## 10. 旧路径 → 目标路径映射建议（仅建议，未执行）

> 目标结构方向：`src/requirement_agent/{api,mcp,workers,domain,application,agents,skills,workflows,infrastructure,config}`

| 当前路径 | 当前职责 | 建议目标路径 | 迁移方式 | 影响范围 | 风险 |
|---|---|---|---|---|---|
| `src/domain/` | 领域 dataclass | `src/requirement_agent/domain/` | 原样迁移 | 低（无外部依赖） | 低 |
| `src/application/` | 应用服务/编排 | `src/requirement_agent/application/` | 原样迁移 | 中（被 interfaces/mcp/http 引用） | 中 |
| `src/agents/` | 4 个 Agent | `src/requirement_agent/agents/` | 原样迁移 | 中 | 中 |
| `src/skills/` | 5 个 Skill + prompts | `src/requirement_agent/skills/` | 原样迁移 | 中 | 中 |
| `src/graph/` | LangGraph 两个子图+节点 | `src/requirement_agent/workflows/` | 拆分后迁移（agents_nodes/commit_nodes 可并入 workflows） | 高 | 中 |
| `src/infrastructure/` | DB/LLM/Embedding/Parser/Storage/Vector/Worker | `src/requirement_agent/infrastructure/` | 原样迁移 | 高 | 中 |
| `src/config/` | settings | `src/requirement_agent/config/` | 原样迁移 | 高（全项目 import） | 中 |
| `src/common/` | time/snowflake | `src/requirement_agent/common/`（目标结构未列，需人工确认） | 保留兼容转发 | 高 | 中 |
| `src/interfaces/http/` | HTTP 路由 | `src/requirement_agent/api/` | 拆分后迁移（rest.py/agent_chat.py/routes.py/schemas.py/_state.py） | 高 | 高 |
| `src/interfaces/mcp/` | MCP 工具+auth | `src/requirement_agent/mcp/` | 原样迁移 | 中 | 中 |
| `apps/worker/` + `src/infrastructure/worker/` | Worker 入口 + 消费循环 | `src/requirement_agent/workers/` | 拆分后迁移 | 中 | 中 |
| `apps/mcp/` | MCP 宿主 | `src/requirement_agent/mcp/`（入口合并） | 保留兼容转发 | 低 | 低 |
| `apps/api/` | 冗余 API 壳 | 建议删除或并入口 | 需要人工确认 | 低（测试引用它） | 低 |
| `main.py` | 根入口 | `src/requirement_agent/api/main.py` | 保留兼容转发（根 main.py 留转发） | 高 | 高 |
| `scripts/` | 运维脚本 | `scripts/`（保留） | 原样迁移 | 低 | 低 |
| 根目录 `需求管理Agent.yml` 等 | 待确认 | — | 需要人工确认 | 低 | 低 |

> 注意：目标结构未包含 `common/` 与 `scripts/`，需人工确认归属；`graph/`→`workflows/` 需评估 import 影响。

---

## 11. 高风险迁移点

1. **`src/config/settings.py` 被全项目 import**（几乎所有模块 `from src.config.settings import settings`）——移动后需全量改 import，风险最高。
2. **`src/common/`（time/snowflake）** 被仓库/Provider 广泛引用；目标结构未规划该目录。
3. **LangGraph 图对象**为模块级单例（`graphs.py:66-67` `analysis_graph = build_analysis_graph()`），移动包路径不影响运行时（编译期），但 import 链长。
4. **`scripts/reindex_embeddings.py`** 硬编码 `src.*` import 路径；移动后脚本需同步改。
5. **`interfaces/http` → `api`**：rest/agent_chat/schemas 相互引用密集，拆分风险高。
6. **未提交的 3 个文件**（snowflake.py/007/测试）——迁移前必须先提交，否则一切无从谈起。

---

## 12. 当前发现的问题

### P0
1. **`src/common/snowflake.py`、`migrations/007_snowflake_ids.sql`、`tests/unit/test_snowflake.py` 未提交**，但生产代码已引用 → 全新 clone HEAD 无法启动、迁移链缺失。（`代码事实`）

### P1
2. **HTTP 层无鉴权**：`API_AUTH_TOKEN` 已配置但无任何端点/中间件使用（`代码事实`；此前 `http/auth.py` 为死代码已删）。
3. **`analysis_mode`（strict/balanced/broad）参数被接受但从未用于控制逻辑**（`代码事实`，`agent_chat.py` 仅透传存储）。
4. **`.env` 中 `OPENAI_*`（qwen3.7-max/DashScope）已配置但不生效**（`LLM_PROVIDER=deepseek`），易误导。（`代码事实`）
5. **`prompts.py` 常量未被 Skill 引用**，三个 Skill 各自内联重复 prompt → 漂移风险。（`代码事实`）

### P2
6. **分片参数两处不一致**：`add_chunks` 默认 600/80 vs 实际调用 600/120。（`代码事实`）
7. **无向量索引**（4096 维精确扫描），量级上升需规划。（`代码事实`）
8. **无 LLM 重试/限流/成本可观测**；多 `LLMProvider` 实例无共享 client。（`代码事实`）
9. **`apps/worker` 未部署**（compose 无 worker 服务；实际由根 app 的 consumer 循环代消费）。（`代码事实`）
10. **`feishu_client.py` stub 返回 `source_type='feishu'`，而 schema 枚举不含 feishu**，接线即 422。（`代码事实`）

### P3
11. `.idea/*` IDE 文件被 git 跟踪；根目录 `需求管理Agent.yml`(87KB)/`问题清单.md` 用途待确认。
12. README（Python 3.11+）与 `pyproject.toml`（>=3.12）不一致。

---

## 13. 明确未修改的内容（第 0 阶段零改动）

- ❌ 未删除任何文件（含看起来像临时/冗余的文件）
- ❌ 未移动/重命名任何文件
- ❌ 未修改任何 Python / SQL / 配置 / Prompt / Skill / 前端
- ❌ 未修改 `.env`
- ❌ 未修改数据库结构与数据（仅运行了只读单测与编译检查；`storage/uploads` 仍为空）
- ❌ 未修改 API / MCP / Agent / LLM 配置
- ❌ 未执行 `git reset --hard` / `git clean -fd` / `rm -rf`
- ✅ 仅新增 1 个报告文件：`docs/refactoring/phase-0-baseline-report.md`（及承载它的 `docs/refactoring/` 目录）

---

## 14. 下一阶段建议（待确认后执行）

1. **立即提交未跟踪的 3 个文件**（snowflake.py / 007 迁移 / test_snowflake.py），消除 P0。
2. 明确目标结构对 `common/`、`scripts/`、`apps/api` 的归属后，再规划 import 迁移顺序（建议：config → common → infrastructure → application → 最后 interfaces）。
3. 迁移前置条件：先跑通一次"全新 clone → 迁移 001-007 → 启动 → 冒烟"以固化基线。
4. 迁移过程用**保留兼容转发**（根 `main.py`/`src/__init__.py` 转发）逐步切换 import，避免一次性大爆炸。
5. 视需要补 E2E 测试与 `.env.example`。

---

## 15. 需要项目负责人确认的问题

1. `src/common/` 在目标结构中的归属（并入 `infrastructure/` 还是保留独立 `common/`）？
2. `apps/api/`（冗余壳）是保留（兼容测试）还是删除并改测试引用？
3. 根目录 `需求管理Agent.yml`（87KB）、`需求管理agent问题清单.md`、`test_main.http` 的用途与去留？
4. 目标结构中 `workflows/` 是否承载现 `src/graph/` 全部内容，还是只拆出 `agents_nodes`/`commit_nodes`？
5. 分片参数统一为 600/120 还是回退 600/80？（当前实际生效 600/120）
6. HTTP 鉴权是否本轮一并接入？（非重构范围但 P1 风险）
7. `.env` 中未生效的 `OPENAI_*` 是否清理？
8. 迁移后是否允许同步新增 `.env.example` 与 E2E 测试？

---

## 结束检查

```text
本阶段是否修改了代码：否
本阶段是否删除了文件：否
本阶段是否移动了文件：否
本阶段是否修改了数据库：否（仅只读单测与编译检查）
本阶段是否改变了 API/MCP：否
本阶段是否改变了 LLM/Agent/Prompt/Skill：否
```

第 0 阶段完成，已停止。等待确认后进入下一阶段。
