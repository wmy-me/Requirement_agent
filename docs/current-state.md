# 项目现状（Current State）

> 最后更新：2026-09-15（新增 **§六 版本模型现状** —— 此前接手的人只能从代码逆推；
> 能力模型方案六批全部落地并验证（见 `docs/方案_需求主线与能力模型.md`），
> 测试基线 251 → **311**。§二 已重写为统一的待办任务清单）
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
| ★ 对话状态机 / Git 式版本管理（**独立方案，不占上表编号**） | 🟡 A/B/C/D/E 五批已完成**且已实测验收**（见 §五），F/G 未开始 | `af56f06`、`3d47629`、`3e85390`、`bbee352`、E 批（迁移 `012`–`014`，**E 批无迁移**） |
| 能力 / 条件模型（**独立方案**） | ✅ **六批全部完成并验证**（见 §二 B6、方案文档 §12–§17） | `28f5f37`（1–2）、`b5ee0d7`（3）、`b311130`（4）、`1eb834e`（5）、`acfbcc9`（6） |

**阶段 2 六项明细**：P1-1 Prompt 去重 ✅ ｜ P1-2 配置卫生 ✅ ｜ P1-3 `analysis_mode` 接线 ✅
｜ P2-1 模型参数透传 ✅ ｜ P2-2 LLM 可观测性 ✅ ｜ P2-3 向量维度决策与守卫 ✅

**★ 七批明细**（方案见 `docs/方案_对话状态机与Git式版本管理.md`）：A 并发隔离 ✅ ｜ B 断点落库 ✅
｜ C 续跑 ✅ ｜ D 模块化 ✅ ｜ **E 合并闭环 ✅** ｜ **F 版本链 DAG ⬜** ｜ **G revert + 乐观锁 ⬜**。
F/G 是「Git 式版本管理」的收尾（§3.3–3.5），**尚未动工**；E 是 F/G 的前置，现已就位。

**测试基线**：`pytest -q` → **311 passed, 2 skipped**（41 个测试文件）。
> 本文先后写过 73（阶段 2 结束）→ 194 → 251（E 批）→ **311**（能力模型六批）。
> 每次加批次都会涨，**以最新一次实测为准**。

**当前结构**：业务代码全部在 `src/requirement_agent/`（导入名 `requirement_agent.*`），
入口 `main.py`（薄壳 → `requirement_agent.api.app`），`src/` 顶层只剩该包。
`apps/`、`src/interfaces/`、MCP 均已删除（可从 git 历史恢复）。

---

## 二、待办任务

> **本节是唯一的工作清单。** 分四类，按「能不能现在动手」排。详细证据在后面各节，这里只给
> 「要做什么 + 卡在哪」。⚠️ 之前的 §二「待拍板」已并入本节 A 类。

| 类别 | 含义 | 条数 |
|---|---|---|
| **A** | 🛑 **等你拍板** —— 不定就动不了 | **3**（A1 已解，见下） |
| **B** | ✅ **已定计划、待实施** —— 前置已就位，可直接开工 | 6 |
| **C** | ⏸️ **挂起** —— 你已明确说先不做 | 1 |
| **D** | 🔧 **技术债 / 已知缺陷** —— 不阻塞，但会积累 | 11（见 §三） |

---

### 2.1 🛑 A 类：等你拍板

**A1 · ~~能力模型的 4 个结构问题~~ —— ✅ 已全部拍板并落地**（2026-09-15）

保留在此仅作决策记录；结论见 `docs/方案_需求主线与能力模型.md` §9。原文如下：

| # | 当时的问题 | 最终结论 |
|---|---|---|
| §9-1 | 设计图里没有「模块」 | **模块与能力正交**，不新建 module 表；`feature.module_key` 保持独立 |
| §9-2 | `capability_id` 单列装不下多能力需求 | 能力挂 feature 的 **N:M**（`feature_capability`） |
| §9-3 | `version_constraints` 缺「修饰哪个能力」的维度 | 条件在抽取记录里与能力配对，归属某个能力 |
| §9-4 | 「业务对象」字段不存在 | 抽取新增 `business_object`（批次 2 已落地） |

> 方案文档另有 4 条非阻塞歧义（§9-5 能力是否也受控 / §9-6 审核记录要不要直连版本 /
> §9-7 feature 要不要加向量 / §9-8 `version_label` 与 `version_title` 分工），可一并定。

**A2 · 鉴权到底什么时候做**

`require_api_auth()`（`config/settings.py:170`）**全仓零调用方**：`API_AUTH_TOKEN` 在 `.env` 与
`.env.example` 里都配了，但没有任何代码校验它；HTTP 层也没有鉴权中间件（`api/app.py` 的
middleware 只加安全响应头）。而那个对外暴露的 Webhook 端点**已经上线了**（§四）——
它现在完全靠飞书自身的验签兜底。

> **注意时态**：这几项原本记作「阶段 3 动工前要先定」，实际是**阶段 3 照常施工、这些都没定**。
> 它们不是「门口的路障」，而是**已经带着债务上线的、仍未处理的决定**。A2 因为端点已真实对外
> 暴露，紧迫性反而变高了。

**A3 · `/health` 重复注册 + OpenAPI tags 双层重复**

两个 `/health`：`api/app.py:131` 与 `api/routes/system.py:20`（前者是**死路由**：`include_router`
先注册了后者，Starlette 首个匹配胜出，所以它永不执行，却又覆盖了 OpenAPI schema）；
tags 重复：`api/router.py:32`（`rest_router`）与 `:51`（`router`）两级都带
`tags=["requirements"]`，实测叠加成 `['requirements','requirements']`（影响 40 条路由）。
**修正会变更 OpenAPI，需明确授权后再动。**

**A4 · 根目录 `需求管理Agent.yml`（87KB，仍被 git 跟踪）去留**

它是一份 Dify 应用 DSL 导出（`mode: advanced-chat`，含两个模型节点），与代码库零引用。
保留 / 删除 / 移入 `docs/`？（删除可从 git 恢复，零风险。）

> ~~Docker 部署是否保留~~ —— **已决（2026-09-14）：`deploy/docker-compose.yml` 已删除，Docker
> 不作为部署目标。** 该文件的构建链本来就是断的（引用的 `Dockerfile` 与 `deploy/migrate.py`
> 在仓库中都不存在，无法 build）。若将来要容器化需从零补。原文件可从 git 历史恢复。

---

### 2.2 ✅ B 类：已定计划、待实施

| # | 事项 | 前置状态 | 备注 |
|---|---|---|---|
| B1 | ★ **F 批 · 版本链 DAG**（§3.3）`merged_from` 两列 + trace 补字段 + 前端时间轴 | ✅ **就位** | E 批已写入 `diff_payload.target_requirement_key`，F 直接读 |
| B2 | ★ **G 批 · revert + `lock_version` 乐观锁**（§3.4/3.5） | ✅ **就位** | `plan_sync(prune=True)` 已备好，revert 直接复用。⚠️ **并发问题已因 E 开放合并入口而可达**（两人同时合并同一 REQ 是 last-write-wins） |
| B3 | **阶段 5 · 影响分析的传播计算** | ⚠️ **有卡点** | 不是缺表：**没有 `depends` 边的生产者**。现有的边全是「相似/冲突」，无方向可传播。需先定依赖关系从哪来 |
| B4 | **阶段 6 · 剩余三项** | 部分就位 | RBAC（等 A2）／数据保留（零实现，六张表无限增长）／**渠道输出闭环**（触发点与凭证已在位：`app_id`/`app_secret` 已注入 `FeishuClient` 却从未被读取） |
| B5 | **阶段 7 · 完整回归 + 生产验收** | — | 建议放最后；`tests/e2e/` 仍为空 |
| B6 | ~~能力模型批次~~ | ✅ **已完成** | 六批全部落地并验证，见 `docs/方案_需求主线与能力模型.md` §12–§17。收尾两件未做：**浏览器验证**、**主线判定建议** |

---

### 2.3 ⏸️ C 类：挂起

**C1 · 飞书渠道接入** —— 你 2026-09-15 明确「**先保留方案，等其他功能流程完善了再做**」。

代码侧钩子已就位（见 §四），用户侧需提供的东西（飞书自建应用 App ID/Secret、
`im:message:send_as_bot` 权限、测试用 `open_id`；完整闭环还需公网 HTTPS 地址与事件订阅）
已调研清楚。**关键区分**：出站（系统→飞书 API）不需要公网可达，入站（飞书→webhook）必须要。

---

### 2.4 🔧 D 类：技术债 / 已知缺陷

全部 11 条已核实，见 **§三** 的表格。其中几条会在后续批次里被顺带解决：

| 遗留 | 会被谁顺带解决 |
|---|---|
| #1 飞书未联调 / #1b `source_type` 枚举未放宽 | C1（挂起中） |
| #8 `/api/v1/ops/*` 无鉴权 | A2 定了之后 |
| #9 相似度阈值粗校准 | 与 B3（影响分析）、B6（能力抽取）都有耦合 |
| #2 E2E 测试为空 | B5 |
| #6 雪花 ID 精度风险 | 未安排，属对外契约变更，需授权 |

---

## 三、仍未解决的遗留（均已核实）

| # | 问题 | 位置 / 证据 |
|---|---|---|
| 1 | **飞书渠道代码已就绪，但未与真实飞书应用联调** | 端点 `POST /api/v1/channels/feishu/webhook`（`api/routes/channels.py`）；协议实现见 `infrastructure/channels/feishu_client.py`。**未验证项**：解密/签名按官方文档实现但无官方测试向量，单测是自洽回环；URL 校验、加密回调、签名头是否与真实飞书一致，需要配一个测试应用实测。 |
| 1b | **`source_type` 对外枚举未放宽** | `api/schemas/agent.py:15,53` 与 `api/schemas/requirements.py:20` 仍为 `web/email/meeting/manual`。渠道入库走 service 不经该校验，所以**功能上不阻塞**；但若要让 `feishu` 能经 `/requirements/submit` 等端点提交，需放宽（属对外契约变更，需授权）。 |
| 2 | **E2E 测试为空** | `tests/` 下只有 `unit/` 与 `integration/`（合计 41 个文件），原本的 `tests/e2e/` 已在 `9ae7b2d` 删除。**没有任何测试真的启动前端跑一遍**，前端改动只能靠 §五 的浏览器手动清单验。 |
| 3 | **worker 未部署** | `workers/tasks.py` 提供了独立的 FastAPI 入口（`python -m requirement_agent.workers`，:8200，含 `/tasks/embedding/process`、`/tasks/document-chunk/process`、`/tasks/requirement-analysis/process`、`/tasks/dead-letter`），但没有任何编排或部署配置。当前 outbox 消费由 API 进程的 lifespan 承担（`api/app.py`）。**注意有两个 `worker` 包**：`infrastructure/worker/`（任务实现 + outbox，被引用的那个）与 `workers/`（仅 HTTP 入口薄壳 + `__main__.py`）。 |
| 4 | **`requirements/ingest` 与 `memory` 路由未下沉 service** | `api/routes/requirements_write.py` 直接调 `object_storage.upload`；`api/routes/memory.py` 内联 `embedding_service.embed`；`api/routes/conversations.py` 的 finalize 内联 `summarize_text` / `memory_extractor`。`complex-routes-analysis.md` 曾要求先下沉再迁移，实际是整文件搬移。 |
| 5 | 无共享 HTTP client | `openai_provider` 每次调用直接 `httpx.post`，未复用连接池。 |
| 6 | **雪花 ID 经 JSON number 传给前端有精度风险** | 后端把 `source_id` 序列化为 JSON number，前端用 JS `Number` 承载，而雪花 ID 普遍超过 `2^53`（`MAX_SAFE_INTEGER`）。当前库里这几个 ID 恰好能被 double 精确表示才没出事；一旦不巧，前端会发出一个不存在的 ID。修法是后端把该字段序列化成字符串（契约变更，需授权）。 |
| 7 | **需求库筛选下拉的候选项来自当前结果集** | 无 facets 接口，选项由返回行聚合而来；只在「无筛选」时刷新，避免一筛选项就只剩当前命中值。代价：**首次加载前**（或结果为空时）下拉是空的。要彻底解决需加一个 distinct 值接口。 |
| 8 | **`/api/v1/ops/*` 的死信重投/放弃没有鉴权** | 与现有全部写端点处境相同（阶段 6 的鉴权批次尚未做），**不是新增暴露面**，但接入鉴权时必须一并纳入保护范围。 |
| 9 | **相似度阈值仍是粗校准，且该模型的相似度基线偏高** | 实测「语义无关」的中文业务文本余弦可达 **0.788**（甘特图排期 vs 报表导出），而 duplicate 阈值是 0.80 —— 只差 0.012，随时可能假阳性。当前值（0.80/0.72/0.60）只是把常见噪声挡在外面，**不是可靠的分界线**。已把「达到阈值就补判」的规则全部拆掉（判断权交回模型），阈值本身待库里有几十条需求后重跑校准。**2026-09-15 走查再次印证（另一条 run 同样测到 0.789），并暴露了它的副作用**：`related` 门槛 0.72 会把模型判为 `related: true` 但候选只有 0.66/0.61 的结果**全部丢弃**，导致关系表长期 0 行 —— 阈值不只影响准确性，还直接决定这个功能有没有产出。 |
| 10 | `apps/mcp` 删除后 IDE 里残留失效运行配置 | 个人配置未入库，手动删即可。 |

> 已在本轮或此前修复、无需再追的：分片参数双标（已统一 600/120）、`analysis_mode` 死参数、
> `OPENAI_*` 误导、prompts 内联重复、snowflake 三文件未提交、sandbox 缺失的 `.env.example`、
> **`src/requirement_agent/tools/` 整个包（2026-09-16 删除）**。
>
> 最后一条值得记一句：它是 MCP 移除时「逐字保留」下来的 7 个工具方法，此后**从未接线**——
> 包外零引用、启动不加载、零测试。`TOOL_ACTOR_ID` 也随之作废并已删除。
> **§四 原写的「内部入口 `tools.ingest_channel_event()`」是错的**：那个名字不在任何 `__all__` 里，
> 根本 import 不到；已在 §四 指向真实的 `ChannelIngestService.ingest()`。盘点见
> `docs/refactoring/archive/deprecated-modules.md`。

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
| 内部入口 | `application/channel_service.py::ChannelIngestService.ingest()`（不必等 Webhook 即可调用）；单例装配点在 `api/dependencies.py` |

**⚠️ 该端点是全项目唯一对外暴露且不经 HTTP 鉴权的路径**，安全性完全依赖飞书自身的
签名校验与 Verification Token（`FEISHU_VERIFICATION_TOKEN` / `FEISHU_ENCRYPT_KEY`，
至少配一个，否则端点直接返回 503）。**将来上全局鉴权中间件（阶段 6）必须把本路径豁免。**

配置见 `.env.example` 的「飞书渠道」段。

---

## 五、对话状态机 / 并发隔离 / 版本管理 —— 能力与验证

> 实施方案见 `docs/方案_对话状态机与Git式版本管理.md`。五批（A–E）已实现，
> 对应迁移 `012`/`013`/`014`（**E 批无迁移**）。下个接手的人想知道「这些能力还能不能用」，
> 照这个清单验。
>
> **⚠️ 该方案还差收尾三节。** §3「Git 式版本管理」共五节，已落地 §3.1（模块化 = D 批）
> 与 §3.2（合并闭环 = E 批）；**§3.3 版本链 DAG（F）、§3.4 revert / §3.5 `lock_version`
> 乐观锁（G）未动工**。所以现在**能合并、能按模块管功能，但版本链还画不出来、也不能回滚**。
> 详见下方「未实现的批次」。

### 真实数据验收（2026-09-15 走查）—— A/B/C/D 全部通过

> 在此之前，本节的能力**只有单测证据**，而单测挡不住「整条链路没通」。2026-09-15 对活着的
> 实例（:8888）做了一次完整走查：新提交一条需求 → 真实分析 → 审核 → 验库。**四项全过。**
>
> **一条重要背景**：走查前查库发现，**「已完成」的能力在真实数据里几乎零证据**——
> `requirement_relation` 0 行、所有 feature `module_key` 全 NULL、3 条需求全停在 v1。
> 原因不是功能坏，而是**数据都比功能上线早**（迁移 `014` 是 09-15 16:19 才落地的）。
> 所以「零证据」不等于「不能用」；下面的走查才是真结论。

| 能力 | 实测结果 | 证据 |
|---|---|---|
| **A 并发隔离** | ✅ 两半都对 | 同会话并发 → **409** 且 detail 带 `active_run`，**未**创建第二个 run；跨会话并行 → **200**，完整跑完 extract→retrieve→analyze→risk |
| **B 断点落库** | ✅ | 12s 处切断的 run，库里留下 `analysis`/`extracted`/`candidates` **三阶段**断点，状态 `running` |
| **C 续跑** | ✅ | `/resumable` → `steps_done:3`；`/resume` **只跑 `resume`+`risk`**（跳过已算三阶段），23s 产出叙事 |
| **D 模块化** | ✅ | 12 个 feature **全部带正确 `module_key`/`module_name`**（巡检任务/问题上报/统计报表/权限管理） |
| 阶段 5 关系写入 | ✅ **首次产出** | `REQ-000015 → REQ-000002, related, 0.7494, proposed` —— **该库有史以来第一行关系数据** |
| 阶段 4 读接口 | ✅ | relations/features/versions/trace/diff 全 200；返回体**含** `module_key`/`module_name` |
| 异步链路 | ✅ | outbox 事件 5s 内被消费，向量 3 → 4，全 4096 维 |

**⚠️ 本次走查的三条限制（勿当成已验）**

1. **僵尸阈值是「模拟」的**：真实 `CHAT_RUN_STALE_TIMEOUT_SECONDS=1800`（30 分钟），实际用的是
   `expire_stale_runs(older_than_seconds=0)` —— **同一个函数，只改了阈值**，真实计时未验。
2. **浏览器 UI 完全没验**：只确认了 `static/js/app.js:1134` 有 `📦 ${f.module_name}` 的渲染代码，
   没有真实像素验证。下方「浏览器手动验证」表**仍然全部待验**。
3. **飞书渠道没碰**（需真实凭据，见 §三 遗留 1）。

**走查中发现的两条实现约束（值得后来人知道）**

- `agent_run` 上有 `BEFORE UPDATE` 触发器 `trg_agent_run_updated_at`，**每次 UPDATE 都强制
  `updated_at = NOW()`**。所以「把行拨回过去来模拟超时」这条路走不通，只能显式传阈值。
- **关系写不写入，由阈值说了算，不是模型说了算**：一次走查里模型判 `related: true`，但其候选
  相似度是 0.662 / 0.607，全部低于 strict 的 `related=0.72`，于是按 `_analysis_relations` 的
  规则 #2 **一条都不写**。只有当候选真的过线（这次 0.7494）才落表。这与 §三 遗留 9 的
  阈值担忧是同一件事的两面 —— **关系功能的产出量对阈值极度敏感**。

**走查留下的数据**：`REQ-000015`（门店巡检管理系统，12 feature 带模块标签、1 条关系）、
source `225548081754537984`、2 个会话与若干 run。**这是本库唯一一条带模块标签 + 关系的数据，
删掉这两项能力就重新回到「零证据」状态**，建议保留作演示样本。

### 能力清单

| 批 | 新增能力 | 落点 |
|---|---|---|
| **A** | 同一对话一次只答一个；跨对话可并行；崩溃留下的僵尸 run 自动清理 | 迁移 `012` 唯一索引 + 409 + `expire_stale_runs` |
| **B** | 每个 LLM 阶段完成即把产物落进 `agent_run.checkpoint`，断开不白算 | 迁移 `013` 的 `stage`/`checkpoint` 列 |
| **C** | 「继续上次分析」从断点续跑，不重算已完成步骤 | `POST /runs/{id}/resume`、`pause`、`GET /chat/{sid}/resumable` |
| **D** | 抽取按模块分组，feature 带 `module_key`/`module_name` | 迁移 `014` + 抽取提示词 + `_module_lines` |
| **E** | 审核时可把来源**并入既有 REQ**：先看预合并预览，再决定；重复关系随之确认 | `domain/feature_diff.py` + `GET /reviews/{id}/merge-preview` + `sync_features` 重写 |

### 自动验证（已写进测试，一条命令）

```
pytest -q   → 311 passed
```

| 测试文件 | 验证的能力 | 关键断言 |
|---|---|---|
| `test_conversation_isolation.py` | A | 同对话二次活跃被拒（409）、跨对话并行、僵尸清理后对话恢复、新 run 不误杀 |
| `test_run_checkpoint.py` | B | 断点逐阶段累积、部分更新不清旧断点、done |
| `test_run_resume.py` | C | **哨兵断言四步 agent 一个不被调用**（续跑不重算）、端点校验 404/409 |
| `test_feature_module.py` | D | `create_features` 落 module 列后直接查库确认 |
| `test_feature_diff.py` | E | **中间插入一行只产出 1 条 add、其余全 keep**（旧缺陷的直接回归）；模块改名不退化；有模块 + 新行无模块 → 保留模块 |
| `test_feature_sync.py` | E | 打在**真实实现**上：插入后其它行的 `content`/`content_hash`/`provenance` 逐字未变；模块列真的落库；**预览与落库逐条一致**且预览不写库 |
| `test_merge_preview.py` | E | 端点的分组/summary；**预览前后功能行一行不变**；404/409/422 |
| `test_requirement_relation.py`（增补） | E | 合并目标不在重复候选 → **一条都不确认**；related/conflict 不被误确认；自环永不产生 |
| `test_merge_relation.py` | E | `confirm_many` 幂等、把 proposed/dismissed 升级为 confirmed；`upsert_many` 的 DO NOTHING 语义未被破坏 |

### 接口验证（curl）

**A · 并发隔离**
```bash
# 同一对话发第二条流式请求（第一条还在跑）→ 应 409，detail 带正在跑的 run
curl -i -X POST :8888/api/v1/agent/chat/stream -H 'Content-Type: application/json' \
  -d '{"message":"问题一","session_id":"<会话id>","client_message_id":"a"}'
```

**C · 续跑**
```bash
curl :8888/api/v1/agent/chat/<会话id>/resumable          # → 返回 run + steps_done
curl -i -X POST :8888/api/v1/agent/runs/<run_id>/resume  # → SSE，且很快（跳过已算步骤）
```

**D · 模块化**
```bash
# 提交一份可分模块的需求 → 审核通过 → feature 表应带 module 列
SELECT content, module_key, module_name FROM requirement_feature;
```

**E · 合并闭环**（2026-09-15 已实测，见下方「E 批实测结果」）
```bash
# 1) 预合并预览（纯读，不写任何东西）
curl "localhost:8888/api/v1/reviews/<source_id>/merge-preview?target_requirement_key=REQ-000015&merge_mode=union"
#    merge_mode=replace 会列出删除清单并给出 warnings
# 2) 带目标合并提交
curl -X POST localhost:8888/api/v1/reviews/submit -H 'Content-Type: application/json' \
  -d '{"source_id":<source_id>,"decision":"approved","target_requirement_key":"REQ-000015","merge_mode":"union"}'
# 3) 验库：目标 REQ 版本 +1、功能只增不减、目标**没有被改名**
SELECT m.current_version, m.requirement_name,
       (SELECT count(*) FROM requirement_feature f WHERE f.requirement_id=m.id AND f.status='active')
FROM requirement_master m WHERE m.requirement_key='REQ-000015';
```

### E 批实测结果（2026-09-15，真实数据）

提交一条与 `REQ-000015` 高度相似的需求（分析给出 `duplicate: true`、相似度 **1.0000**），
走了一遍「预览 → 合并 → 验库」：

| 检查点 | 结果 |
|---|---|
| 预览**不写库** | 调用前后目标仍是 v1 / 12 条功能 / 1 个版本 ✅ |
| 预览结论 | `add 1 · keep 12 · delete 0`，按 4 个模块分组，`active_after 13` ✅ |
| 合并后 | v1 → **v2**，功能 12 → **13**，`change_type=add`，`parent_version_no=1` ✅ |
| **没有级联改写** | v2 的 `feature_changes` **只有 1 条 add、0 条 modify**；`provenance` 里带 v2 的只有新增那条 —— 旧实现在这里会产生一批 modify 并覆写内容 ✅ |
| 模块标签穿过合并 | 新增的 `F-013` 带 `module_key=巡检任务模块` ✅ |
| 目标**没被改名** | 仍是「门店巡检管理系统 V1.0 需求」，没被来源标题覆盖 ✅ |
| 溯源 | `diff_payload.target_requirement_key=REQ-000015`、`merge_mode=union` ✅ |

⚠️ **关系确认这条路径本次没有触发，属预期行为**：选项 A 的规则是「只有**其它**重复候选
过了 `duplicate` 阈值（0.80）才确认」。本次候选里只有 `REQ-000015` 自己过了线（1.0），
其余是 0.74 / 0.71 / 0.65 —— 所以一条都没确认。该路径由
`test_requirement_relation.py` / `test_merge_relation.py` 覆盖。

**浏览器待验（前端无自动化测试）**：待办卡片出现「相似需求候选」→ 点「合并进这个 REQ」→
预览按模块列出增删改 → 勾「以来源为准」看到红色删除行与告警 → 通过 → toast 显示
「已并入 REQ-000015」→ 需求库打开 REQ-000015 看到 v2 与新增功能行。

### 浏览器手动验证（前端无自动化测试）

| 场景 | 步骤 | 预期 |
|---|---|---|
| 跨对话并行 | 会话 A 提问（分析中）→ 新建对话 → 在 B 提问 | B 能开始，不被 A 卡住（改前被 `if (state.streaming) return` 拦） |
| 切回内容不丢 | A 分析中切到 B、再切回 A | A 内容完整（stream buffer 回填） |
| 继续卡片 | 分析未完成时断开/刷新 → 回该会话 | 「上次分析未完成（N/4 步）+ 继续」卡片 |
| 模块标签 | 打开一条带模块的 REQ 详情 | 功能明细行有 📦 模块名 |

⚠️ **数据前提已满足**：D 的模块标签与 C 的继续卡片此前「无数据可验」（历史数据无
stage/checkpoint/module），现在走查已造出带模块的 `REQ-000015` 与带断点的 run。
**上表四条仍全部是「待验」——缺的是浏览器实操，不是数据。**

### 未实现的批次（F/G）—— 现状与依赖

| 批 | 计划内容 | 依赖 | 现在能不能做 |
|---|---|---|---|
| **F** | 版本链 DAG（§3.3）：`merged_from` 两列 + trace 补字段 + 前端时间轴。**E 批已把 `diff_payload.target_requirement_key` 写好了，F 直接读** | E ✅ | ✅ 可开工 |
| **G** | revert（§3.4）+ `lock_version` 乐观锁（§3.5，该列当前是死的）。`plan_sync(prune=True)` 已就位，revert 直接复用 | E ✅ | ✅ 可开工 |

> ⚠️ **G 的并发问题现已可达**：E 批开放了合并入口，两个审核人同时合并进同一个 REQ
> 就是 last-write-wins（后者静默覆盖前者）。这一条在 E 批的拍板里被明确**留给 G 批**。

---

## 六、版本模型现状

> 这一节记录**版本链实际长什么样**。此前没有这节，接手的人只能从代码逆推。
> 数据取自 REQ-000015 的真实版本链（2026-09-15）。

### 6.1 三张表怎么配合

```
requirement_master        一条需求（主线）—— requirement_key = REQ-000015
  current_version = 3     ← 当前版本号
  final_requirement       ← 当前 active 功能的拼接

requirement_version       版本快照（每版一行）
  version_no / version_title / change_type(new/add/modify/delete)
  requirement_snapshot    该版本**完整正文快照**（不是增量）
  change_summary          审核人写的变更说明
  feature_changes         本版功能级变更清单（add/modify/delete）
  capability_snapshot / constraint_snapshot   本版的能力与条件快照
  parent_version_no / status(draft/pending_review/current/superseded)
  superseded_by_version_no

requirement_feature       功能条目（**跨版本存活的行**）
  origin_version_no       第几版引入
  removed_version_no      第几版移除（NULL = 还在）
  provenance              变更史 [{version_no, source_id, kind}]
```

**关键点：版本是快照，功能是长命行。** 一条功能从 v1 活到 v3，存在区间由
`origin_version_no` / `removed_version_no` 划定，改过的历史记在 `provenance`。

### 6.2 真实版本链（REQ-000015）

| | 状态 | 类型 | 父 | 快照 | 变更 | 能力快照 | 标题 |
|---|---|---|---|---|---|---|---|
| v1 | superseded | new | — | 227字 | 12条 | 0条 | 门店巡检管理系统 V1.0 需求 |
| v2 | superseded | add | v1 | 238字 | 1条 | 0条 | …V1.1 需求 |
| **v3** | **current** | add | v2 | 269字 | 3条 | **5条** | …V1.3 巡检任务模块补充需求 |

版本 ← 来源也已链上（v1←source#…754537984、v2←…#308797440、v3←…#578448896）。
v1/v2 能力快照为 0 条，因为它们建于能力模型之前——**空着比编一份更干净**；
v1 的「被谁取代」为空，它是迁移回填时标 superseded 的，那时该列刚建出来。

### 6.3 入口

| 端点 | 作用 |
|---|---|
| `GET /requirements/{key}/versions` | 版本历史 |
| `GET /requirements/{key}/features?at_version=N` | **某个版本当时**的功能集 |
| `GET /requirements/{key}/diff?from=&to=` | 两版之间 added/removed/modified/unchanged |
| `GET /requirements/{key}/trace` | 需求 + 逐版本快照 + 每版来源链 |
| `GET /requirements/{key}/capabilities` | 当前版本的能力与条件 |

详情页有四个区块：能力与条件 / 当前功能明细 / **版本 Diff** / **版本历史** / 需求关系。

### 6.4 已经做到的

- **每次版本变化都经过人工审核** —— 版本只在 `commit_requirement_node` 里产生，没有别的入口
- **能看版本前后改了什么**：`/diff` 逐条给 added/removed/modified
- **审核时先看再提交**：合并预览按模块列出 add/modify/delete
- 每版**完整快照**，词表日后改名也不污染历史
- **一条主线只有一个 current** —— 数据库部分唯一索引在守，不靠应用自觉

### 6.5 还缺什么

| 缺的 | 说明 |
|---|---|
| **多个候选标题** | 一条需求只有一个标题；「同一需求的不同视角入口」不存在 |
| **文档版本链** | `document_asset` 只有 checksum 去重，**没有版本概念**；同名同格式文档改一个字会变成两个互不相干的资产。**方案已写：`docs/方案_文档版本链.md`** |
| ~~主线判定建议~~ | ✅ **已做**（`a85ee9f`）：分析输出 `suggestion{action,target,confidence,reason}`，**只出建议、人工确认** |
| **行内高亮** | diff 只到「功能条目」粒度，没有内容片段级对比 |

**未验证**：详情页四个区块**从未在浏览器里点过**；`/diff` 端点除被详情页调用外，
没做过端到端验证。

---

## 七、文档导航

| 文档 | 用途 |
|---|---|
| `docs/current-state.md`（本文） | 真实进度 + **§二 统一的待办任务清单** —— **先看这个** |
| `docs/方案_需求主线与能力模型.md` | **能力 / 条件 / 需求主线**的设计方案：现状分析、现有模型映射、迁移方案、8 条设计歧义（**§9 的 4 条卡住 schema，待拍板**） |
| `docs/方案_对话状态机与Git式版本管理.md` | 对话状态机/并发隔离/Git 版本管理的**完整设计方案与批次**（A–G 全量，含未做的 E/F/G） |
| `docs/History/需求规格.md` | 需求规格原稿 |
| `docs/History/数据模型与实施史.md` | 数据模型的演进与实施记录 |
| `docs/History/工程史附录.md` | 工程史附录 |
| `docs/方案_文档版本链.md` | **文档侧版本链**的方案（**未动工**）：现状、三个卡点、分批、4 条待拍板 |
| `docs/api-contract.md` | **前端接口契约** —— 每个端点的字段名与形状（实测核对过）。**重写前端前先看这个** |
| `README.md` | 环境搭建、常用接口、目录结构 |
| `docs/refactoring/archive/*` | 阶段 0/1 施工快照与决策依据（**进度信息已过时**） |
| `migrations/README.md` | 迁移清单 + 向量维度与索引的决策 |
