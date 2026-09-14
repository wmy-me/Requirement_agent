# 重构进度盘点与下一阶段方案（阶段 1 · 结构重组）

> 生成日期：2026-09-11 ｜ 依据：当前工作区实际代码（唯一事实源）｜ **本文档只读盘点，未修改任何代码**
> 用途：交由技术顾问审查，据此生成下一阶段指令。

---

## 一、总体阶段进度

| 阶段 | 状态 | 说明 |
|---|---|---|
| 阶段 0：代码盘点与测试基线 | ✅ 完成 | `docs/refactoring/phase-0-baseline-report.md` |
| **阶段 1：项目结构重组** | 🟡 **进行中（约 35%）** | 包骨架 + API 层迁移进行中，见 §二 |
| 阶段 2：LLM/Agent/Prompt/Skill/Embedding/检索配置整改 | ⬜ 未开始 | |
| 阶段 3：ChannelAdapter 与飞书 Webhook | ⬜ 未开始 | |
| 阶段 4：需求库表格、组合筛选、CSV 导出 | ⬜ 未开始 | |
| 阶段 5：需求关系表与影响分析 | ⬜ 未开始 | |
| 阶段 6：RBAC、数据保留、可观测性、渠道输出闭环 | ⬜ 未开始 | |
| 阶段 7：完整回归与生产验收 | ⬜ 未开始 | |

> 说明：你要求"现在是代码迁移"，即**当前处于阶段 1（结构重组）**，且只做完了 API 层的一部分。

---

## 二、阶段 1 详细进度（代码迁移）

### 2.1 已完成
| 子批次 | 内容 | 产物 |
|---|---|---|
| — | 包骨架 | `src/requirement_agent/`（10 子包）+ 顶层兼容包 `requirement_agent/` |
| — | 统一入口 | `src/requirement_agent/api/app.py`；`main.py` 薄壳；`apps/api/*` 保留 |
| 3.1 | Schema | `api/schemas/{common,requirements,reviews}.py`（4 个 Schema） |
| 3.2.1 | 只读需求查询组 | `api/routes/requirements.py`（versions/features/diff/trace） |
| 3.2.2 | 相似需求检索组 | `api/routes/requirements.py`（search/features-search，`search_router`） |
| 3.2.3 | 健康检查组 | `api/routes/health.py`（health/db、health/llm） |
| 3.2.4 | 来源追踪 + 审计 | `api/routes/sources.py`、`api/routes/audit.py` |
| 3.2.5 | 审核只读 | `api/routes/reviews.py`（pending、{id}/detail） |
| 3.3.2 | Agent 分析管线 | `api/routes/agent.py`（`/agent/run`） |
| 3.3.3 | 审核提交 | `api/routes/reviews.py`（`submit_router`，`/reviews/submit`） |

### 2.2 迁移地图
- **已迁移路由：14 条**（分布在新包 6 个 route 模块）
- **未迁移路由：26 条**（rest.py 21 条 + agent_chat.py 5 条）
- 迁移方式统一为「**一个正式实现（新包）+ 旧模块 include_router 兼容转发**」，实测旧/新函数为**同一对象**。

### 2.4 迁移原则：内容零改动（已逐字验证）✅

**核心原则（本项目铁律）**：迁移**只改结构，不改方法内容**——迁移前后每个函数的**源码逐字一致**，
旧模块通过 `include_router` + 兼容转发复用同一实现（旧/新路径为**同一对象**，`is` 为 True）。

**验证方式**：用 `git show HEAD:<旧文件>` 取出迁移前原始函数体，与新文件逐字比对。
对已迁移的 **14 个函数**执行，结果：

```
✅ database_health / llm_health / list_requirement_versions / list_requirement_features /
   get_requirement_diff / get_requirement_trace / search_requirements / search_requirement_features /
   get_source_trace / list_audit_events / list_pending_reviews / get_review_detail /
   submit_review_decision / run_agent_pipeline
结论：全部函数体与迁移前逐字一致 = True
```

**少数"形态变化"（仍属结构层，行为等价）**：
| 项 | 变化 | 是否等价 |
|---|---|---|
| `main.py` | 由 78 行应用构建 → 17 行薄壳；原构建代码**逐字移入** `api/app.py` | ✅ 行为等价（同一 app 实例、同端口/中间件/静态） |
| Schema 4 个类 | 定义**逐字移入** `api/schemas/*`，旧 `schemas.py` 转发 | ✅ 同一类对象；仅 `__module__` 路径变化，字段/校验器/OpenAPI 不变 |
| `agent_chat.py` | `run_agent_pipeline` 定义移出，改为 import | ✅ 同模块 `chat_with_agent` 仍调用同一函数 |

> 后续所有批次继续遵循此原则：**新文件 = 旧文件对应内容的逐字副本**；旧模块只保留转发/壳。

### 2.3 新包实际结构
```
src/requirement_agent/
├── __init__.py
├── api/
│   ├── app.py                 # 统一 FastAPI app（唯一来源）
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── requirements.py    # 只读查询组 + search_router（6 路由）
│   │   ├── health.py          # 2 路由
│   │   ├── sources.py         # 1 路由
│   │   ├── audit.py           # 1 路由
│   │   ├── reviews.py         # 只读 2 + submit 1
│   │   └── agent.py           # /agent/run
│   └── schemas/{__init__,common,requirements,reviews}.py
├── domain|application|agents|skills|workflows|infrastructure|config|mcp|workers/  ← 仅空 __init__.py 骨架
└── (顶层兼容包) requirement_agent/  → 转发到 src.requirement_agent
```

---

## 三、当前冻结基线（改动前必须先记录）

| 指标 | 值 |
|---|---|
| 分支 / HEAD | `master` / `5c5386f` |
| compileall | ✅ 通过 |
| 单元测试 | **42 passed / 2 skipped** |
| 全量测试（含集成） | **43 passed / 2 skipped** |
| 可路由路径总数 | 42（含 `/ui`、`/static`、重复的 `/health`） |
| `len(app.routes)` / `len(router.routes)` | 8 / 2 |
| 入口导入 | main / apps.mcp.server / apps.worker.tasks 均 OK |

---

## 四、未迁移清单（阶段 1 剩余工作）

### 4.1 `src/interfaces/http/rest.py`（21 条）
| 分组 | 路由 | 备注 |
|---|---|---|
| 根/健康 | `GET /`、`GET /health` | `/health` 与 app 版重复（既有） |
| 需求 | `GET /requirements`、`POST /requirements/submit`、`POST /requirements/ingest` | ingest 含文件上传/解析/MinIO/outbox（**需拆分**） |
| 文档 | `GET /documents`、`GET /documents/search`、`GET /documents/{id}`、`GET /documents/{id}/chunks`、`POST /documents/{id}/reindex` | |
| 会话 | `GET/POST /conversations`、`GET/POST /conversations/{id}/messages`、`PATCH/DELETE /conversations/{id}`、`POST /conversations/{id}/finalize` | finalize 内联 LLM |
| 记忆 | `GET/POST /memory`、`POST /memory/{id}/delete`、`GET /memory/context` | POST 内联 embedding |

### 4.2 `src/interfaces/http/agent_chat.py`（5 条）
`POST /agent/chat`、`POST /agent/chat/stream`、`POST /agent/chat/stream-with-files`、`GET /agent/chat/{session_id}`、`GET /agent/runs/{run_id}`（SSE + 线程/队列，建议**整体模块迁移**）

### 4.3 旧基础设施（尚未迁移）
`src/interfaces/http/_state.py`（共享单例）、`schemas.py` 剩余 5 个 Schema、`routes.py` 聚合器、`main.py`（已是薄壳）。

---

## 五、⛔ 阻塞项与风险（需先决策）

| 级别 | 问题 | 影响 | 建议 |
|---|---|---|---|
| ~~P0~~ | `src/common/snowflake.py`、`migrations/007_snowflake_ids.sql`、`tests/unit/test_snowflake.py` 未提交（HEAD 5c5386f 已引用 `src.common.snowflake`） | 全新 clone HEAD 无法启动、迁移链缺 007 | ✅ **已解决**：提交 `962fab5`（3 文件 227 行）；HEAD 现自洽 |
| P1 | HTTP 层**无鉴权**（`API_AUTH_TOKEN` 未生效） | 安全风险 | 归属**阶段 6**（RBAC），届时接入；本阶段不动 |
| P2 | `rest.py` 已堆积未使用导入（`version_repo`/`feature_repo`/`retrieval_service`/`check_database_connection`/`LLMProvider`/`audit_repo`/`source_repo`/`review_service`/`ReviewSubmitRequest`） | 可读性 | 阶段 1 收口时统一清理（已登记 deprecated-modules.md） |
| P2 | `/health` 重复注册、OpenAPI tags 重复（`['requirements','requirements']`） | OpenAPI 有 warning | **重构前即存在**；改动会变更契约，需你授权后单独处理 |
| P3 | `apps/api/` 冗余壳；根目录 `需求管理Agent.yml`(87KB) 等用途待确认 | — | 待你确认 |

---

## 六、下一阶段方案（阶段 1 剩余 · 建议子批次）

> 原则不变：每批次「一个正式实现 + 旧模块兼容转发 + 保持注册顺序/契约 + 测试通过 + 暂停确认」。

| 批次 | 内容 | 目标文件 | 方式 | 风险 |
|---|---|---|---|---|
| **1-A（先做）** | 提交 P0 三文件 + 记录基线 | — | git add 指定文件 | ✅ **已完成**（commit `962fab5`） |
| 3.3.4 | conversations 只读（list、messages） | `api/routes/conversations.py` | 原样迁移 | 低 |
| 3.3.5 | conversations 写（create/update/delete/add_message） | `api/routes/conversations.py`（独立 router，保顺序） | 原样迁移 | 低 |
| 3.3.6 | documents 组（只读 + reindex） | `api/routes/documents.py` | 原样迁移 | 低 |
| 3.3.7 | memory 组（list/context 只读；upsert/delete 写） | `api/routes/memory.py` | 原样迁移（写组独立 router） | 中（POST 内联 embedding） |
| 3.3.8 | requirements/submit | `api/routes/requirements_write.py` | 原样迁移 | 低 |
| 3.3.9 | requirements/ingest（文件上传） | `api/routes/files.py` | **拆分后迁移**（文件处理下沉 service） | 高 |
| 3.3.10 | agent_chat 整体（含 SSE） | `api/routes/agent_chat.py` | **整体模块迁移**（不拆分） | 高 |
| 3.3.11 | 基础设施收口：`_state.py`→`api/dependencies.py`；剩余 5 Schema→`api/schemas/` | — | 拆分/原样 | 中 |
| **3.3.12（收口）** | 清理 rest.py 未使用导入；旧模块瘦身为纯转发；阶段 1 验收 + 提交 | — | — | 中 |

**每批次验收标准**（沿用）：
- 路由集合 / operationId / tags / status_code / Schema **完全一致**（OpenAPI 前后比对）
- 旧/新路径函数为**同一对象**（`is` 为 True）
- 无第二个 app、无重复注册、无循环依赖
- 单测 42/2、全量 43/2 基线不变
- `len(app.routes)`/`len(router.routes)` 不变

---

## 七、阶段 2–7 概览（供顾问排期，本阶段不执行）

- **阶段 2**：LLM/Agent/Prompt/Skill/Embedding/检索配置整改——见 `../../docx/LLM_Agent_Prompt_Skill_审计报告_2026-09-11.md`（含 P0–P3 问题与改进建议：Prompt 去重、参数透传、可观测性、无索引向量检索等）。
- **阶段 3**：ChannelAdapter + 飞书 Webhook（签名/幂等/事件队列）——当前 `feishu_client.py` 为未接线 stub，`source_event_id` 幂等索引已就绪。
- **阶段 4**：需求库表格视图 + 组合筛选 UI + CSV 导出——后端筛选已就绪（`/requirements/search`），缺前端表格与导出端点。
- **阶段 5**：需求关系表 + 影响分析——当前 `relation_type` 仅写 `'source'`，related/conflict 未落库。
- **阶段 6**：RBAC/数据保留/可观测性/渠道输出闭环（含 HTTP 鉴权 P1）。
- **阶段 7**：完整回归与生产验收（补 E2E，当前 `tests/e2e` 为空）。

---

## 八、需项目负责人（你）或顾问确认的问题

1. **先提交 P0 三文件吗？**（`snowflake.py` / `007` / `test_snowflake.py`）——建议 1-A 立即处理，否则 HEAD 处于不可启动状态。
2. **阶段 1 剩余批次的顺序**是否按 §六 建议（会话 → 文档 → 记忆 → 需求写 → ingest → agent_chat → 基础设施收口）？
3. **`requirements/ingest`（文件上传）**：是否同意"文件解析/MinIO 上传下沉到 Application Service"的拆分方式？（否则只能整体搬运，接口层继续持有 IO）
4. **`agent_chat`（SSE）**：是否同意**整模块迁移**（不拆分 SSE 生命周期）？
5. **`/health` 重复注册 / tags 重复**：是否授权在阶段 1 收口时一并修正（会**变更 OpenAPI**，违反"不改契约"，需明确授权）？
6. **未使用导入**：是否同意在收口批次统一清理（不影响行为）？
