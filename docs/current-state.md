# 项目现状（Current State）

> 最后更新：2026-09-14
> 用途：接手本项目时的**第一份文档**。记录真实进度与当前未决事项。
>
> ⚠️ **`docs/refactoring/archive/` 下的进度表写于各阶段施工期间，已过时，勿据此排期。**
> 那里面的「阶段 1 进行中（约 35%）」「映射表 ⏳ 待迁移」等状态均与代码不符，
> 归档仅为保留施工过程与决策依据。

---

## 一、真实进度

| 阶段 | 状态 | 代表提交 |
|---|---|---|
| 0. 代码盘点与测试基线 | ✅ 完成 | 报告见 `docs/refactoring/archive/phase-0-baseline-report.md` |
| 1. 项目结构重组（src-layout） | ✅ 完成 | `df44749` → `1f88419` → `180578b` → `5d2d551` → `17379f4` → `dc91f43` |
| 1′. 收尾：`apps/` 并入、死代码清理 | ✅ 完成 | `d67fe0c`、`9ae7b2d` |
| 2. LLM / Agent / Prompt / Skill / Embedding 整改 | ✅ 完成 | `c554fd5`、`6343d18`、`f03f664`、`0a13127`、`3c99293`、`c1226b6` |
| 3. ChannelAdapter + 飞书 Webhook | 🟡 代码完成，待真实凭据联调 | `949bf22`（地基）、飞书协议部分见下方说明 |
| 4. 需求库表格视图 + 组合筛选 + CSV 导出 | ✅ 完成 | 全宽面板 `#library-panel`；`GET /api/v1/requirements`（含筛选）与 `/export`（CSV） |
| 5. 需求关系表 + 影响分析 | 🟡 关系表已建，影响分析的传播计算未做 | `migrations/009` + 审核通过时写入 + 双向读端点 + 详情页关系块 |
| 6. RBAC / 数据保留 / 可观测性 / 渠道输出闭环 | 🟡 可观测性与死信处理已完成，其余三项未做 | `/api/v1/ops/*` + 请求日志中间件 + 前端「运维」tab |
| 7. 完整回归 + 生产验收 | ⬜ 未开始 | — |

**阶段 2 六项明细**：P1-1 Prompt 去重 ✅ ｜ P1-2 配置卫生 ✅ ｜ P1-3 `analysis_mode` 接线 ✅
｜ P2-1 模型参数透传 ✅ ｜ P2-2 LLM 可观测性 ✅ ｜ P2-3 向量维度决策与守卫 ✅

**测试基线**：`pytest -q` → **73 passed, 2 skipped**。

**当前结构**：业务代码全部在 `src/requirement_agent/`（导入名 `requirement_agent.*`），
入口 `main.py`（薄壳 → `requirement_agent.api.app`），`src/` 顶层只剩该包。
`apps/`、`src/interfaces/`、MCP 均已删除（可从 git 历史恢复）。

---

## 二、待拍板（这几项挡在阶段 3 门口）

阶段 3 要做的是**对外暴露的飞书 Webhook 端点**，下面几项建议在动它之前定下来。

1. **鉴权是否提前到阶段 3**
   `require_api_auth()`（`src/requirement_agent/config/settings.py:149`）**全仓零调用方**：
   `API_AUTH_TOKEN` 在 `.env` 与 `.env.example` 里都配了，但没有任何代码校验它。
   HTTP 层也没有鉴权中间件（`api/app.py` 的 middleware 只加安全响应头）。
   阶段 3 要加的正是对外暴露的 Webhook 端点，建议一并处理。

2. **`/health` 重复注册 + OpenAPI tags 双层重复**
   两个 `/health`：`api/app.py:81` 与 `api/routes/system.py:20`；
   tags 重复：`api/router.py:30` 与 `:46` 两级都带 `tags=["requirements"]`，结果叠加成
   `['requirements','requirements']`。修正会**变更 OpenAPI**，需明确授权后再动。

3. **根目录 `需求管理Agent.yml`（87KB，仍被 git 跟踪）去留**
   它是一份 Dify 应用 DSL 导出（`mode: advanced-chat`，含两个模型节点）。保留 / 删除 / 移入 `docs/`？

4. ~~Docker 部署是否保留~~ —— **已决（2026-09-14）：`deploy/docker-compose.yml` 已删除，Docker 不作为部署目标。**
   该文件的构建链本来就是断的：它引用的 `Dockerfile`（构建上下文 `..`）与 `deploy/migrate.py`
   （`migrate` 服务的 command）**在仓库中都不存在**，README 也没有任何 docker 段落，实际无法 build。
   若将来要容器化，需从零补 Dockerfile、迁移执行脚本与 compose 编排（含 postgres/minio 启动顺序与健康检查）。
   原文件可从 git 历史恢复。

---

## 三、仍未解决的遗留（均已核实）

| # | 问题 | 位置 / 证据 |
|---|---|---|
| 1 | **飞书渠道代码已就绪，但未与真实飞书应用联调** | 端点 `POST /api/v1/channels/feishu/webhook`（`api/routes/channels.py`）；协议实现见 `infrastructure/channels/feishu_client.py`。**未验证项**：解密/签名按官方文档实现但无官方测试向量，单测是自洽回环；URL 校验、加密回调、签名头是否与真实飞书一致，需要配一个测试应用实测。 |
| 1b | **`source_type` 对外枚举未放宽** | `api/schemas/agent.py:15,53` 与 `api/schemas/requirements.py:20` 仍为 `web/email/meeting/manual`。渠道入库走 service 不经该校验，所以**功能上不阻塞**；但若要让 `feishu` 能经 `/requirements/submit` 等端点提交，需放宽（属对外契约变更，需授权）。 |
| 2 | **E2E 测试为空** | `tests/` 下只有 `unit/` 与 `integration/`（集成测试仅 1 个文件），原本的 `tests/e2e/` 已在 `9ae7b2d` 删除。 |
| 3 | **worker 未部署** | `workers/tasks.py` 提供了独立的 FastAPI 入口（:8200，含 `/tasks/embedding/process`、`/tasks/document-chunk/process`、`/tasks/dead-letter`），但没有任何编排或部署配置。当前 outbox 消费由 API 进程的 lifespan 承担（`api/app.py`）。 |
| 4 | **`requirements/ingest` 与 `memory` 路由未下沉 service** | `api/routes/requirements_write.py` 直接调 `object_storage.upload`；`api/routes/memory.py` 内联 `embedding_service.embed`；`api/routes/conversations.py` 的 finalize 内联 `summarize_text` / `memory_extractor`。`complex-routes-analysis.md` 曾要求先下沉再迁移，实际是整文件搬移。 |
| 5 | 无共享 HTTP client | `openai_provider` 每次调用直接 `httpx.post`，未复用连接池。 |
| 6 | **雪花 ID 经 JSON number 传给前端有精度风险** | 后端把 `source_id` 序列化为 JSON number，前端用 JS `Number` 承载，而雪花 ID 普遍超过 `2^53`（`MAX_SAFE_INTEGER`）。当前库里这几个 ID 恰好能被 double 精确表示才没出事；一旦不巧，前端会发出一个不存在的 ID。修法是后端把该字段序列化成字符串（契约变更，需授权）。 |
| 7 | **需求库筛选下拉的候选项来自当前结果集** | 无 facets 接口，选项由返回行聚合而来；只在「无筛选」时刷新，避免一筛选项就只剩当前命中值。代价：**首次加载前**（或结果为空时）下拉是空的。要彻底解决需加一个 distinct 值接口。 |
| 8 | **`/api/v1/ops/*` 的死信重投/放弃没有鉴权** | 与现有全部写端点处境相同（阶段 6 的鉴权批次尚未做），**不是新增暴露面**，但接入鉴权时必须一并纳入保护范围。 |
| 9 | **相似度阈值仍是粗校准，且该模型的相似度基线偏高** | 实测「语义无关」的中文业务文本余弦可达 **0.788**（甘特图排期 vs 报表导出），而 duplicate 阈值是 0.80 —— 只差 0.012，随时可能假阳性。当前值（0.80/0.72/0.60）只是把常见噪声挡在外面，**不是可靠的分界线**。已把「达到阈值就补判」的规则全部拆掉（判断权交回模型），阈值本身待库里有几十条需求后重跑校准。 |
| 9 | `apps/mcp` 删除后 IDE 里残留失效运行配置 | 个人配置未入库，手动删即可。 |

> 已在本轮或此前修复、无需再追的：分片参数双标（已统一 600/120）、`analysis_mode` 死参数、
> `OPENAI_*` 误导、prompts 内联重复、snowflake 三文件未提交、sandbox 缺失的 `.env.example`。

---

## 四、渠道接入现状（阶段 3）

入库链路：**验签（飞书自身）→ 归一化 → 落 source(received) → 入 outbox → 立即返回**，
分析由后台 `RequirementAnalysisTask` 补上。之所以不等分析，是因为飞书事件订阅（HTTP Webhook
与长连接两种模式）都要求在 3 秒内应答，而分析图要跑 4 个 LLM 步骤。

| 组件 | 位置 |
|---|---|
| 通道抽象 | `infrastructure/channels/base.py`（`InboundRequirement` + `ChannelAdapter`） |
| 飞书协议 | `infrastructure/channels/feishu_client.py`（AES-256-CBC 解密 / 签名 / token / 归一化） |
| 接入服务 | `application/channel_service.py`（幂等预查 → 落库 → 入队） |
| 异步消费 | `infrastructure/worker/tasks.py::RequirementAnalysisTask` |
| 端点 | `api/routes/channels.py` → `POST /api/v1/channels/feishu/webhook` |
| 内部入口 | `tools.ingest_channel_event()`（不必等 Webhook 即可调用） |

**⚠️ 该端点是全项目唯一对外暴露且不经 HTTP 鉴权的路径**，安全性完全依赖飞书自身的
签名校验与 Verification Token（`FEISHU_VERIFICATION_TOKEN` / `FEISHU_ENCRYPT_KEY`，
至少配一个，否则端点直接返回 503）。**将来上全局鉴权中间件（阶段 6）必须把本路径豁免。**

配置见 `.env.example` 的「飞书渠道」段。

---

## 五、文档导航

| 文档 | 用途 |
|---|---|
| `docs/current-state.md`（本文） | 真实进度与未决事项 —— **先看这个** |
| `README.md` | 环境搭建、常用接口、目录结构 |
| `docs/refactoring/archive/*` | 阶段 0/1 施工快照与决策依据（**进度信息已过时**） |
| `migrations/README.md` | 迁移清单 + 向量维度与索引的决策 |
