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
| 3. ChannelAdapter + 飞书 Webhook | ⬜ 未开始 | — |
| 4. 需求库表格视图 + 组合筛选 + CSV 导出 | ⬜ 未开始 | — |
| 5. 需求关系表 + 影响分析 | ⬜ 未开始 | — |
| 6. RBAC / 数据保留 / 可观测性 / 渠道输出闭环 | ⬜ 未开始 | — |
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
   `require_api_auth()`（`src/requirement_agent/config/settings.py:149`）**全仓零调用方**，
   而 `deploy/docker-compose.yml:86` 把 `API_AUTH_TOKEN` 列为 `:?` 必填。
   现状是「必须配、配了也不校验」—— 比不配更危险，因为它给人已鉴权的错觉。
   HTTP 层目前没有任何鉴权中间件（`api/app.py` 的 middleware 只加安全响应头）。

2. **`/health` 重复注册 + OpenAPI tags 双层重复**
   两个 `/health`：`api/app.py:81` 与 `api/routes/system.py:20`；
   tags 重复：`api/router.py:30` 与 `:46` 两级都带 `tags=["requirements"]`，结果叠加成
   `['requirements','requirements']`。修正会**变更 OpenAPI**，需明确授权后再动。

3. **根目录 `需求管理Agent.yml`（87KB，仍被 git 跟踪）去留**
   它是一份 Dify 应用 DSL 导出（`mode: advanced-chat`，含两个模型节点）。保留 / 删除 / 移入 `docs/`？

4. **Docker 部署是否保留**
   现状：`deploy/docker-compose.yml` 引用的 `Dockerfile`（构建上下文 `..`）与
   `deploy/migrate.py`（`migrate` 服务的 command）**在仓库中都不存在**，
   `deploy/` 目录下只有这一个 yml；README 也没有任何 docker 段落。**compose 实际无法 build。**
   若 Docker 部署已弃用，正确动作是删掉该文件而不是补文件。

---

## 三、仍未解决的遗留（均已核实）

| # | 问题 | 位置 / 证据 |
|---|---|---|
| 1 | **飞书渠道未接线**，且 stub 返回的 `source_type='feishu'` 不在对外枚举内 | `infrastructure/channels/feishu_client.py:23`；枚举见 `api/schemas/agent.py:15,53`，取值为 `web/email/meeting/manual`。注意 DB 层 `source_type TEXT` **无约束**，冲突只在 Python 契约层。飞书事件走哪条入库路径属阶段 3 的设计决定。 |
| 2 | **E2E 测试为空** | `tests/` 下只有 `unit/` 与 `integration/`（集成测试仅 1 个文件），原本的 `tests/e2e/` 已在 `9ae7b2d` 删除。 |
| 3 | **worker 未进 compose** | compose 服务只有 postgres / migrate / minio / minio-init / api；`workers/` 包有 `__main__.py` 却无编排。当前 outbox 消费由 API 进程的 lifespan 承担。 |
| 4 | **`requirements/ingest` 与 `memory` 路由未下沉 service** | `api/routes/requirements_write.py` 直接调 `object_storage.upload`；`api/routes/memory.py` 内联 `embedding_service.embed`；`api/routes/conversations.py` 的 finalize 内联 `summarize_text` / `memory_extractor`。`complex-routes-analysis.md` 曾要求先下沉再迁移，实际是整文件搬移。 |
| 5 | 无共享 HTTP client | `openai_provider` 每次调用直接 `httpx.post`，未复用连接池。 |
| 6 | `apps/mcp` 删除后 IDE 里残留失效运行配置 | 个人配置未入库，手动删即可。 |

> 已在本轮或此前修复、无需再追的：分片参数双标（已统一 600/120）、`analysis_mode` 死参数、
> `OPENAI_*` 误导、prompts 内联重复、snowflake 三文件未提交、sandbox 缺失的 `.env.example`。

---

## 四、阶段 3 起点的已知地形

- **`feishu_client.py` 是未接线 stub**（`infrastructure/channels/`），返回 `source_type='feishu'`。
- **幂等索引已就绪**：`migrations/001_init_business_schema.sql:26-28`
  对 `requirement_source(source_type, source_event_id)` 建了唯一索引（`WHERE source_event_id IS NOT NULL`），
  渠道事件重复投递可直接依赖它去重。
- **尚无** Webhook 端点、签名校验、事件队列。
- 阶段 3 之前建议先看 **§二** 的四项拍板，尤其鉴权。

---

## 五、文档导航

| 文档 | 用途 |
|---|---|
| `docs/current-state.md`（本文） | 真实进度与未决事项 —— **先看这个** |
| `docs/refactoring/weekend-quickstart.md` | 新机环境搭建（仍然有效） |
| `docs/refactoring/archive/*` | 阶段 0/1 施工快照与决策依据（**进度信息已过时**） |
| `migrations/README.md` | 迁移清单 + 向量维度与索引的决策 |
