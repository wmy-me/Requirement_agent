# 项目现状（Current State）

> 最后更新：**2026-09-16**。**§二 已重写为当前待办清单**（已完成的批次不再列进去，
> 只留一行汇总）。当天落地了 T1/T2（雪花 ID 序列化统一 + 契约验证脚本）与 F 批（版本链可视化）。
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
| ★ 对话状态机 / Git 式版本管理（**独立方案，不占上表编号**） | ✅ **A–G 七批全部完成**（见 §五） | `af56f06`、`3d47629`、`3e85390`、`bbee352`、E 批（迁移 `012`–`014`）、G/F 批（**均无迁移**） |
| 能力 / 条件模型（**独立方案**） | ✅ **六批全部完成并验证**（见 §二 B6、方案文档 §12–§17） | `28f5f37`（1–2）、`b5ee0d7`（3）、`b311130`（4）、`1eb834e`（5）、`acfbcc9`（6） |

**阶段 2 六项明细**：P1-1 Prompt 去重 ✅ ｜ P1-2 配置卫生 ✅ ｜ P1-3 `analysis_mode` 接线 ✅
｜ P2-1 模型参数透传 ✅ ｜ P2-2 LLM 可观测性 ✅ ｜ P2-3 向量维度决策与守卫 ✅

**★ 七批明细**（方案见 `docs/方案_对话状态机与Git式版本管理.md`）：A 并发隔离 ✅ ｜ B 断点落库 ✅
｜ C 续跑 ✅ ｜ D 模块化 ✅ ｜ E 合并闭环 ✅ ｜ **F 版本链可视化 ✅** ｜ **G revert + 乐观锁 ✅**（F/G 均为 2026-09-16）。

> ⚠️ **F 批实施时发现方案 §3.3 的 `merged_from_*` 前提不成立**（合并是「来源 → REQ」、
> 来源还没有 requirement_key，所以那两列会恒等于自己）。改为画**溯源时间轴**：
> 每版显示 `change_type` 色点、前驱、**以及真实的来源链**（`/trace` 的 `sources[]`，
> 此前从没被渲染过）。详见方案 §3.3(a)。

**测试基线**：`pytest -q` → **561 passed, 2 skipped**。
> 本文先后写过 73（阶段 2 结束）→ 194 → 251（E 批）→ 311（能力模型六批）→ 393（G 批）→ 412（T1/T2 与 F 批）→ 434（工具层批次 1）→ 412（批次 1 回退后）→ 466（按四层重建的批 1）→ 505（批 2）→ **535**（B1 鉴权）。
> 每次加批次都会涨，**以最新一次实测为准**。

**当前结构**：业务代码全部在 `src/requirement_agent/`（导入名 `requirement_agent.*`），
入口 `main.py`（薄壳 → `requirement_agent.api.app`），`src/` 顶层只剩该包。
`apps/`、`src/interfaces/`、MCP 均已删除（可从 git 历史恢复）。

---

## 二、待办任务

> **本节是唯一的工作清单。** 按「能不能现在动手」分五类；详细证据在后面各节与各方案文档，
> 这里只给「要做什么 + 卡在哪」。
>
> **已完成的批次不再列进来**（否则清单只会越写越长）—— 见 §一 的进度表、
> §五 的验证记录，以及下方 2.5 的一行汇总。

### 📍 接续点（2026-09-16 收工）

**最近动了什么**（都已提交、测试 **412 绿**）：
1. **T1/T2** —— 雪花 ID 全端点一律字符串化；`scripts/verify_api_contract.py` 进仓库
2. **F 批** —— 版本链可视化（溯源时间轴 + 每版来源链 + diff 版本选择）。⚠️ **发现方案 §3.3
   的 `merged_from` 前提不成立**，改成了溯源，详见方案 §3.3(a)
3. **流程文档 + 分层分析** —— `docs/流程_需求从提交到入库.md`、`docs/分析_工具分层现状与越层调用.md`
4. **工具层批次 1 已回退** —— 旧版分层错了（封装 Repository 而非 Service），已删除。
   现按「严格四层」重建，**待你定分析文档 §6 的三处**，然后开工批 1

**下一步的三个候选**（没有先后，看你想做哪个）：

| 候选 | 工作量 | 为什么值得做 |
|---|---|---|
| **工具层批次 2** | 中 | 把工具接线到现有 agents（工具清单降级成 prompt 说明文本，**不碰 `LLMProvider`**）。⚠️ 方案 §5 还有 **4 条待拍板**，第 ② 条「工具不能写库」我已按建议落地并写进测试，**你没明确认过** |
| **B9 固化 UI 验证脚本** | **小（半天）** | F 批首次用无头 Chrome + CDP 真的验了前端（方法留在 `/tmp`，**会被清掉**）。固化成 `scripts/verify_ui_smoke.py` 后，§五 那 4 条挂了很久的「待验」也能顺带补上 |
| **B7 回滚的前端入口** | 小 | G 批做的 `/revert` **只有后端**，没入口等于没做 |

**还有一条等你一句话的**：current-state §三 遗留 **#11** —— `/features?at_version=99`
静默返回当前功能集。修法要定语义：**越界返回 404 还是 409**？定了就是几行。

> 未 push：本地 `master` 领先 `origin/master` 2 个 commit（工具层方案 + 批次 1）。

| 类别 | 含义 | 条数 |
|---|---|---|
| **A** | 🛑 **等你拍板** —— 不定就动不了 | **3** |
| **B** | ✅ **已定计划、待实施** —— 可以直接开工 | **9** |
| **C** | ⏸️ **挂起** —— 你已明确说先不做 | 1 |
| **N** | 🆕 **新能力** —— 批次 1 已落地，2/3 未动 | 1 |
| **D** | 🔧 **技术债 / 已知缺陷** | 11（见 §三） |

---

### 2.1 🛑 A 类：等你拍板

**A1 · ~~鉴权到底什么时候做~~ —— ✅ 已完成（2026-09-17，B1）**

原状：`require_api_auth()` 全仓零调用方，`main.py` 绑 `0.0.0.0:8888`，**全部写端点裸奔**。

现已落地（`api/auth.py` + `api/app.py` 中间件）：
**六个权限档次**（read / analyze / submit / review / revert / ops）×
**三个角色**（admin / reviewer / system_worker），**角色由服务端从 token 解析、不可伪造**；
401 与 403 分开且带 `request_id` + `required_scope`；飞书 webhook 与探活/静态/文档豁免；
**没配 token = 全部 401（默认拒绝）**。契约见 `docs/api-contract.md` §1.2。

⚠️ **部署时必须在 `.env` 配 `API_AUTH_TOKENS`**（或沿用旧的 `API_AUTH_TOKEN`），
否则前端全部拿到 401 —— 这是刻意的，见契约 §1.2 的说明。

**A2 · `/health` 重复注册 + OpenAPI tags 双层重复**

两个 `/health`：`api/app.py:131` 与 `api/routes/system.py:20`（前者是**死路由**：`include_router`
先注册了后者，Starlette 首个匹配胜出，所以它永不执行，却又覆盖了 OpenAPI schema）；
tags 重复：`api/router.py:32`（`rest_router`）与 `:51`（`router`）两级都带
`tags=["requirements"]`，实测叠加成 `['requirements','requirements']`（影响 40 条路由）。
**修正会变更 OpenAPI，需明确授权后再动。**

**A3 · 根目录 `需求管理Agent.yml`（87KB，仍被 git 跟踪）去留**

它是一份 Dify 应用 DSL 导出（`mode: advanced-chat`，含两个模型节点），与代码库零引用。
保留 / 删除 / 移入 `docs/`？（删除可从 git 恢复，零风险。）

> ~~Docker 部署是否保留~~ —— **已决（2026-09-14）：`deploy/docker-compose.yml` 已删除，
> Docker 不作为部署目标。** 该文件的构建链本来就是断的（引用的 `Dockerfile` 与
> `deploy/migrate.py` 在仓库中都不存在，无法 build）。若将来要容器化需从零补。

---

### 2.2 ✅ B 类：已定计划、待实施

| # | 事项 | 前置状态 | 备注 |
|---|---|---|---|
| B1 | **阶段 5 · 影响分析的传播计算** | ⚠️ **卡在业务定义** | 不是缺表：**没有 `depends` 边的生产者**，现有边全是「相似 / 冲突」、无方向可传播。要先定「依赖关系在业务上由谁产生」 |
| B2 | **阶段 6 · 剩余三项** | 部分就位 | RBAC（等 A1）／**数据保留**（零实现，六张表无限增长）／**渠道输出闭环**（`FeishuClient` **只有入站方法**，`app_id`/`app_secret` 存了却从未被读取 —— 不是「接线」，是要新写发送能力） |
| B3 | **阶段 7 · 完整回归 + 生产验收** | — | 建议放最后；`tests/e2e/` 仍为空 |
| B4 | **契约 T3 · 补齐没覆盖的端点** | ✅ 可直接开工 | 11 组端点（health / conversations / documents / memory / audit / sources / 审查详情 / 提交 / 对话非流式 / 运维死信 / 飞书 webhook）列在 `docs/api-contract.md` §10-T3，扩 `SPEC` 即可 |
| B5 | **契约 T4 · 三个「空的形状」造数据验证** | 🟡 **卡在数据** | `/constraints`（词表 0 行）、`capability_match.constraints.matched`、`titles.highlight` 四种 kind —— **形状从未被运行时验证过**。拿到实测前**前端别照文档示例写死** |
| B6 | **契约 T5 · 前端接线缺口** | 看前端排期 | `analysis.suggestion` 后端出了、前端没渲染；`/requirements/{key}/titles` 前端 0 调用（数据也 0 行）；需求库筛选下拉为空（`departments`/`sensitivity_levels` 实测是 `[]`） |
| B7 | **回滚端点的前端入口** | ✅ 可直接开工 | `POST /requirements/{key}/revert` **只有后端**（G 批做的），前端要走这条路径得先有入口 |
| B8 | **文档版本链批 3–5** | ⚠️ **有风险** | 批 1–2 已落地（`document_stream` + 分片哈希），但**写入路径未动**、新列对 API 完全不可见。批 3 改上传路径是本方案唯一有风险的一批 |
| B9 | **把 UI 验证固化成脚本** | ✅ 可直接开工 | F 批第一次用「无头 Chrome + CDP」验前端（见 §五），方法留在 `/tmp`（**会被清掉**）。固化成 `scripts/verify_ui_smoke.py`，顺手把 §五 那 4 条「待验」补上 |

---

### 2.3 ⏸️ C 类：挂起

**C1 · 飞书渠道接入** —— 你 2026-09-15 明确「**先保留方案，等其他功能流程完善了再做**」。

代码侧钩子已就位（见 §四），用户侧需提供的东西（飞书自建应用 App ID/Secret、
`im:message:send_as_bot` 权限、测试用 `open_id`；完整闭环还需公网 HTTPS 地址与事件订阅）
已调研清楚。**关键区分**：出站（系统 → 飞书 API）不需要公网可达，入站（飞书 → webhook）必须要。

> 调研时还发现一条**对你可能最划算**的用法：CowAgent 用**飞书交互卡片**做审批
> （按钮回调 = 审核动作，几乎不用自建前端）。本项目人工审核是核心流程，值得看一眼。

---

### 2.4 🆕 N 类：新能力（需要先写方案）

**N1 · Agent 工具层** —— 你想做的那件。**已重新定调为「严格四层设计」。**

- **方案**：`docs/方案_Agent工具层.md`（CowAgent 结构调研 + 契约/注册表设计 + 分批）
- **现状盘点**：`docs/分析_工具分层现状与越层调用.md`（分层映射 + 越层调用清单 + 与你四层规则的逐条比对）
- **⚠️ 2026-09-16：批次 1 的代码已回退。** 那一版实现成「薄封装 **Repository**」，
  而四层设计要求只读工具封装 **Application Service / Query Service** —— **分层错了**。
  同时它「看起来被测着、实际没有」：11 条单测只覆盖注册表与 schema，
  **没有一个调用真实工具的 `execute`**（实测改坏底层方法名，22 条测试全绿而工具已坏）。
  删除比修补干净。**旧实现本身也随历史清理一并移除**（2026-09-16：那一对「加了又删」的净零 commit 被丢弃，原提交在本地标签 `backup/pre-clean` 里仍可查），设计记录保留在方案 §7

**按四层重建的批次**（分析文档 §7）：

| 批 | 内容 | 状态 |
|---|---|---|
| **0** | 删零调用方的旧 `tools/` | ✅ **已完成**（2026-09-16） |
| **1** | ✅ **已完成**（2026-09-16）：`application/requirement_query.py`（只读 QueryService）+ 工具契约 + **5 个样板工具** + 54 条测试（含 **23 条真实 `execute`**）。验证报告见分析文档 §8 | — |
| **2** | ✅ **已完成**：共 **15 个**只读工具（13 个查询类 + 2 个预演类）。⚠️ **与规划文档有两处刻意的差异**（`requires_confirmation` 不加；「第二批分析工具」5 个里只加 2 个 —— 分界线是「调不调模型」），理由见分析文档 §10 | — |
| **3** | ✅ **已完成**（2026-09-17，B3）：`retrieve_node` 接入注册表 + 统一调用入口 + 超时生效 + 调用留痕。⚠️ **检索失败不降级**（刻意的），理由见分析文档 §12.2 | — |
| **4** | 裁决类写下沉到 Application Service（能力/关系/标题 6 处）—— §6.1 已选 **(c) 只约束需求主线**，这 6 处**属于周边裁决**，按 (c) 可以不下沉 | ⬜ 待重新确认 |
| **5** | 再评估是否需要 L4（模型可调用 Adapter） | ⬜ |

**不做**：不重构工作流、不引入 function calling、不动事务边界。

**现状：系统里没有任何「工具」概念** —— 无注册表、无 schema、`LLMProvider` 只支持
`generate` / `generate_stream` / `embed`，**不支持 function calling**。所有 Agent 都是
「prompt 进 → JSON 出」的单轮模式，检索等步骤是**编排里写死的**、模型看不见也选不了。

参照对象已调研（`/home/wangmengyang/Software/CowAgent`，见对话记录）：工具层本身很薄——
契约（类属性 + 一个 `execute`）+ 名录（`__all__`）+ 注册表（**存类不存实例**）+ 一工具一包，
加一个工具只要 2 个文件 + 1 行注册。**难的在循环外围的三层防护**：消息完整性
（每个 `tool_use` 必须有配对的 `tool_result`，`finally` 兜底）、上下文预算
（裁剪只在循环开始前做一次、以完整轮次为单位）、失效兜底（防重复调用阈值 + 重试 + 降级链）。

建议分三步，**前两步不依赖 function calling、没有失败模式**：
① 工具契约 + 注册表（把散落的能力收成可枚举的工具）→ ② 接线到现有 agents
（工具清单先降级成 prompt 里的说明文本）→ ③ 真 function calling。

---

### 2.5 ✅ 已完成（不再列进清单）

> 只留一行汇总。详细验证记录见 §五 与各方案文档。

| 批次 | 完成于 | 一句话 |
|---|---|---|
| 能力 / 条件模型（**六批全完成**） | 2026-09-15 | 能力词表、抽取匹配、版本快照、按能力反查、人工裁决、存量回填 |
| ★ 对话状态机 / Git 式版本管理（**A–G 七批全完成**） | 2026-09-16 | 并发隔离、断点续跑、模块化、合并闭环、**revert + 乐观锁**、**版本链可视化** |
| **T1 + T2**（雪花 ID 序列化统一 + 契约验证脚本） | 2026-09-16 | ID 一律字符串；`scripts/verify_api_contract.py` 进仓库、可挂 CI |
| 死代码清理 | 2026-09-16 | 删除从未接线的 `src/requirement_agent/tools/` 整个包（盘点见 `deprecated-modules.md`） |
| 能力模型的 4 条设计歧义 | 2026-09-15 | 已拍板并落地，结论见 `docs/方案_需求主线与能力模型.md` §9 |

---

## 三、仍未解决的遗留（均已核实）

| # | 问题 | 位置 / 证据 |
|---|---|---|
| 1 | **飞书渠道代码已就绪，但未与真实飞书应用联调** | 端点 `POST /api/v1/channels/feishu/webhook`（`api/routes/channels.py`）；协议实现见 `infrastructure/channels/feishu_client.py`。**未验证项**：解密/签名按官方文档实现但无官方测试向量，单测是自洽回环；URL 校验、加密回调、签名头是否与真实飞书一致，需要配一个测试应用实测。 |
| 1b | **`source_type` 对外枚举未放宽** | `api/schemas/agent.py:15,53` 与 `api/schemas/requirements.py:20` 仍为 `web/email/meeting/manual`。渠道入库走 service 不经该校验，所以**功能上不阻塞**；但若要让 `feishu` 能经 `/requirements/submit` 等端点提交，需放宽（属对外契约变更，需授权）。 |
| 2 | **E2E 测试为空**（**部分缓解**） | `tests/` 下只有 `unit/` 与 `integration/`，原本的 `tests/e2e/` 已在 `9ae7b2d` 删除。**没有进 pytest 的前端测试**。2026-09-16 起有了一条替代路径：无头 Chrome + CDP 驱动页面自己的函数、读回 DOM 并截图（F 批首次使用，见 §五）。**但它还是一次性脚本、没固化**（→ §二 B9）。 |
| 3 | **worker 未部署** | `workers/tasks.py` 提供了独立的 FastAPI 入口（`python -m requirement_agent.workers`，:8200，含 `/tasks/embedding/process`、`/tasks/document-chunk/process`、`/tasks/requirement-analysis/process`、`/tasks/dead-letter`），但没有任何编排或部署配置。当前 outbox 消费由 API 进程的 lifespan 承担（`api/app.py`）。**注意有两个 `worker` 包**：`infrastructure/worker/`（任务实现 + outbox，被引用的那个）与 `workers/`（仅 HTTP 入口薄壳 + `__main__.py`）。 |
| 4 | **`requirements/ingest` 与 `memory` 路由未下沉 service** | `api/routes/requirements_write.py` 直接调 `object_storage.upload`；`api/routes/memory.py` 内联 `embedding_service.embed`；`api/routes/conversations.py` 的 finalize 内联 `summarize_text` / `memory_extractor`。`complex-routes-analysis.md` 曾要求先下沉再迁移，实际是整文件搬移。 |
| 5 | 无共享 HTTP client | `openai_provider` 每次调用直接 `httpx.post`，未复用连接池。 |
| 7 | **需求库筛选下拉的候选项来自当前结果集** | 无 facets 接口，选项由返回行聚合而来；只在「无筛选」时刷新，避免一筛选项就只剩当前命中值。代价：**首次加载前**（或结果为空时）下拉是空的。要彻底解决需加一个 distinct 值接口。 |
| 8 | **`/api/v1/ops/*` 的死信重投/放弃没有鉴权** | 与现有全部写端点处境相同（阶段 6 的鉴权批次尚未做），**不是新增暴露面**，但接入鉴权时必须一并纳入保护范围。 |
| 9 | **相似度阈值仍是粗校准，且该模型的相似度基线偏高** | 实测「语义无关」的中文业务文本余弦可达 **0.788**（甘特图排期 vs 报表导出），而 duplicate 阈值是 0.80 —— 只差 0.012，随时可能假阳性。当前值（0.80/0.72/0.60）只是把常见噪声挡在外面，**不是可靠的分界线**。已把「达到阈值就补判」的规则全部拆掉（判断权交回模型），阈值本身待库里有几十条需求后重跑校准。**2026-09-15 走查再次印证（另一条 run 同样测到 0.789），并暴露了它的副作用**：`related` 门槛 0.72 会把模型判为 `related: true` 但候选只有 0.66/0.61 的结果**全部丢弃**，导致关系表长期 0 行 —— 阈值不只影响准确性，还直接决定这个功能有没有产出。 |
| 10 | `apps/mcp` 删除后 IDE 里残留失效运行配置 | 个人配置未入库，手动删即可。 |
| 11 | **`/features?at_version=N` 对越界版本静默返回当前功能集** | 2026-09-16 做工具层时实测发现：REQ-000015 当前是 V3，传 `at_version=99` **返回 16 条（= 当前）**，看起来像「v99 长这样」。根因在仓储的 SQL 判据 `origin_version_no <= N AND (removed_version_no IS NULL OR > N)` —— 对**未来版本**它对所有当前行都成立，所以仓储本身自洽，是**接口没做范围校验**。工具层已在自己的 `get_features` 里拦住；**接口层未修**（改法是读 `current_version` 越界返回 404 或 409，**属语义决策**）。详见 `docs/方案_Agent工具层.md` §7.4 |

> 已在本轮或此前修复、无需再追的：分片参数双标（已统一 600/120）、`analysis_mode` 死参数、
> `OPENAI_*` 误导、prompts 内联重复、snowflake 三文件未提交、sandbox 缺失的 `.env.example`、
> **`src/requirement_agent/tools/` 整个包（2026-09-16 删除）**、
> **`/requirements/features/search` 每次 500（2026-09-16 修复）**、
> **雪花 ID 精度风险（2026-09-16 修复，原 6 条）**。
>
> 「雪花 ID」那条补两句：它原本记作「修法是序列化成字符串（契约变更，**需授权**）」，
> 后来授权做了（T1）—— 而且**不是只改审核端点，是全部端点一律字符串**，
> 连同前端那处 `Number(id)` 的写法一起改掉（不然正好抵消）。当时已经有真实反例躺在库里：
> `outbox_event.id = 225548242094391297`，`int(float())` 变成 `...296`。
> 现在由 `scripts/verify_api_contract.py` 的数据库级扫描守着。
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

> 实施方案见 `docs/方案_对话状态机与Git式版本管理.md`。**七批（A–G）全部完成**，
> 对应迁移 `012`/`013`/`014`（**E/F/G 三批无迁移**）。下个接手的人想知道
> 「这些能力还能不能用」，照这个清单验。
>
> **本节按时间堆积了三次验收记录**（2026-09-15 的 A–D 走查、E 批合并实测、G 批实测）。
> 它们仍然有效，但读的时候注意日期 —— 越靠下的越新。

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
   没有真实像素验证。**这条限制到 2026-09-16 才部分解除**：F 批开始用无头 Chrome + CDP
   验前端（见下方「浏览器手动验证」表 —— 版本时间轴与 diff 两条已验，其余四条仍未验）。
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
pytest -q   → 412 passed
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

> **G/F 批新增的测试不在上表**（那是 A–E 时期写的）：`test_feature_history.py`（16，纯函数边界）、
> `test_feature_history_restore.py`（11）、`test_master_optimistic_lock.py`（7）、
> `test_review_commit_path.py`（4，**首次覆盖真实提交路径**）、`test_requirement_revert.py`（12）、
> `test_version_timeline_shape.py`（7）、`test_id_serialization.py`（12）。

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

**浏览器待验（E 批，2026-09-15 记）**：待办卡片出现「相似需求候选」→ 点「合并进这个 REQ」→
预览按模块列出增删改 → 勾「以来源为准」看到红色删除行与告警 → 通过 → toast 显示
「已并入 REQ-000015」→ 需求库打开 REQ-000015 看到 v2 与新增功能行。
**这条至今未验。**

### 浏览器手动验证（前端无自动化测试）

| 场景 | 步骤 | 预期 |
|---|---|---|
| 跨对话并行 | 会话 A 提问（分析中）→ 新建对话 → 在 B 提问 | B 能开始，不被 A 卡住（改前被 `if (state.streaming) return` 拦） |
| 切回内容不丢 | A 分析中切到 B、再切回 A | A 内容完整（stream buffer 回填） |
| 继续卡片 | 分析未完成时断开/刷新 → 回该会话 | 「上次分析未完成（N/4 步）+ 继续」卡片 |
| 模块标签 | 打开一条带模块的 REQ 详情 | 功能明细行有 📦 模块名 |
| **版本时间轴**（F 批） | ✅ **已验**（2026-09-16，无头 Chrome + CDP） | 三个节点 V3/V2/V1；节点颜色按类型不同（v1=new、v2/v3=add）；V3 标「当前」且竖线为主色；每版有「↑ 基于 V{n}」与「← 来源 #… · web · 发起人」；「本版变更 N 条」/「正文快照」默认收起。**页面无 JS 报错** |
| **Diff 版本选择**（F 批） | ✅ **已验**（同上） | 「从/到」两个下拉各含 V1–V3，默认 2→3；列表随选择刷新 |

⚠️ **数据前提已满足**：D 的模块标签与 C 的继续卡片此前「无数据可验」（历史数据无
stage/checkpoint/module），现在走查已造出带模块的 `REQ-000015` 与带断点的 run。
**前四条仍全部是「待验」——缺的是浏览器实操，不是数据。**

> ✅ **F 批那两条是真的验过的**（2026-09-16）：起服务 → 无头 Chrome + CDP（DevTools
> Protocol）打开 `/ui` → 调用页面自己的 `showRequirementDetail()` → 既读回 DOM 断言
> （节点 class、来源文本、下拉选项），也截图肉眼看过。**这是本项目第一次对前端行为做
> 实际验证**，此前全靠人工点。
>
> 方法与一次性脚本留在 `/tmp/fbatch_evidence/`（截图 `timeline.png` + `verify_timeline.py`）。
> ⚠️ **`/tmp` 会被清掉** —— 若要长期用，应固化成 `scripts/verify_ui_smoke.py`
> （需要 chrome 与一个跑着的服务，所以不适合进 pytest，适合单独跑）。

### G 批实测结果（2026-09-16，无迁移）

**G 做了什么**：`lock_version` 乐观锁（方案 §3.5）+ 回滚端点（§3.4）+ **一项方案里漏掉的前置**。

| 检查点 | 结果 |
|---|---|
| **乐观锁真的生效** | 陈旧 `lock_version` 写回 → 抛 `ConcurrentModificationError`，且那一行**逐字段未被写过**（`test_stale_write_after_a_real_commit_is_rejected`）✅ |
| **热路径没被锁误杀** | 新建 REQ → v1 ✅；带目标 REQ 的合并 → v2 ✅；连续两次合并 → v3 ✅（`test_review_commit_path.py`，**此前全仓没有测试跑过真实提交路径**） |
| **期望值取自事务开头** | 单测把 `lock_version=7` 与 `next_version=1` 分开构造，断言传的是 **7 不是 1** —— 抓错位置锁会形同虚设且不会有别的测试变红 ✅ |
| 回滚产出新版本 | v1→v2（modify）→v3（replace 删掉两条）→ 回滚到 v1 → **v4**，`change_type=modify` ✅ |
| **内容真的复原** | 回滚后当前功能集与 `features?at_version=1` **逐字段相等**（含被改过的文字）✅ |
| **复活原行** | 被删的功能回滚后是**同一 id、同一 feature_key**，`feature_capability` 关联保住 ✅ |
| **append-only** | 历史三版的 `requirement_snapshot`/`feature_changes` **逐字段未变**，只多一条 v4 ✅ |
| 同主线一个 current | 由部分唯一索引保证，回滚后仍只有一条 ✅ |
| 回滚闭合差异 | `/diff?from_version=1&to_version=4` → added/removed/modified **全空**、unchanged=3 ✅ |
| 审计与向量 | 写 `requirement_version_reverted` 审计 + `embedding_sync` outbox ✅ |

**方案里漏掉的一步（本次补上）**：`features?at_version=N` 只复原「当时哪些功能存在」，
`content` 拿的是**当前值** —— `modify` 是就地覆写，旧文字不另存。所以照方案 §3.4 直接做回滚，
会得到「回滚了成员、没回滚内容」的假回滚。补法是新增纯函数
`domain/feature_history.py`：沿 `requirement_version.feature_changes` **反向回放**，
把当前行推回任意历史时刻。**它顺带修好了 `/diff` 的 `modified` 恒空**（同根因，见 §六 6.5）。

**两处与方案原文不同的选择**（都是实测后改的）：

1. **回滚不复用 `sync_features(prune=True)`**（方案 §3.4 的原建议）。它是**内容哈希匹配**：
   现有集合一旦混入已软删的同内容行，同内容多候选会挑错配对（一删一活、两条行都错）；
   它的 `delete` 分支还会**无条件重写**已软删行的 `removed_version_no`，静默污染「哪一版删的」
   这段历史。改用 key 驱动的 `reconcile_features`（新增方法，不碰 E 批的内核）。
2. **复活走 UPDATE 原行，不新建**。新建会让 `feature_key` 断裂，更要紧的是能力关联挂在
   `feature_capability.feature_id` 上、而它只认 `status='active'` 的行 —— 新建等于把
   回滚回来的功能的能力标签抹掉。

**新增测试 52 条**（341 → 393）：`test_feature_history.py`（16，纯函数边界）、
`test_feature_history_restore.py`（11）、`test_master_optimistic_lock.py`（7）、
`test_review_commit_path.py`（4）、`test_requirement_revert.py`（12）、
`test_review_service.py` 增补（3）。

**仍未做**：浏览器实操（回滚按钮前端还没接）。`/revert` 端点目前**只有后端**，
前端要走这条路径得先有个入口。

### F 批实测结果（2026-09-16，无迁移）

**做的与方案 §3.3 写的不一样** —— 实施时发现原文前提不成立（详见方案 §3.3(a) 与 §六 6.5）。

| 检查点 | 结果 |
|---|---|
| 时间轴渲染 | 三节点 V3/V2/V1，`change_type` 决定节点颜色，当前版竖线换主色并标「当前」✅ |
| **每版来源链** | 「← 来源 #… · 渠道 · 发起人」——**这条真实关系此前从没被渲染过** ✅ |
| 点击展开 | 「本版变更 N 条」与「正文快照」默认收起（此前无条件铺整段，一版几百字会淹掉时间轴）✅ |
| diff 版本选择 | 「从/到」两个下拉，改选当场刷新（此前前端从不传 from/to，只能看相邻两版）✅ |
| **前端真的验过了** | 起服务 → 无头 Chrome + CDP → 调页面自己的 `showRequirementDetail()` → 读回 DOM 断言 + 截图；**页面无 JS 报错** ✅ |

> ⚠️ **一个仍未处理的细节**：回滚版本的 `change_type` 恒为 `modify`（数据库 CHECK 没有
> `revert`），真正的「这是回滚」记在 `diff_payload.kind="revert"` 里。
> **时间轴目前没有读它** —— 所以回滚产出的版本与一次普通修改，在时间线上长得一样。
> 要么在时间轴上标出来，要么接受这个近似（→ 可并入 §二 B6 的前端接线）。


---

### F 批顺带记录：两个「小缺陷」只修了一个

方案 §3.3(c) 列了两条，实施时只做了第一条：

| 条目 | 状态 |
|---|---|
| 前端 diff 只能看相邻版本（后端早支持 `from`/`to`） | ✅ **已修**：加了「从 / 到」两个下拉，改选当场刷新 |
| `master.status` 的 `archived` / `deleted` **从未被写入** | ⬜ **未动**，且**不该顺手改** |

第二条不做是有理由的：它有两个出路，**都不是「小修」**——要么「接上归档功能」（那是一个
新功能：入口、权限、归档后是否还出现在列表/检索里、能不能取消归档），要么「从枚举里去掉」
（那是**契约变更**：`requirement_master.status` 的 CHECK 约束 + `api-contract.md` §1 的
状态表都要改，且将来真要归档还得加回来）。它也与版本链无关，只是恰好被列在同一节。
**所以留在这里当待办，不混进 F 批。**


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
  （⚠️ 2026-09-16 之前 `modified` **结构上恒空**，见 6.5）
- **审核时先看再提交**：合并预览按模块列出 add/modify/delete
- **能回滚**（2026-09-16 新增）：`POST /requirements/{key}/revert` append-only 地产出新版本，
  被删的功能**复活原行**（`feature_key` 与能力关联都不丢）
- **能按版本读当时的内容**：`features?at_version=N` 的 `content`/`module_*` 回到 N 时刻
  （不再只是「当时哪些行存在」）
- **并发不会静默丢数据**（2026-09-16 新增）：`lock_version` 乐观锁 CAS，
  两个审核人同时合并同一 REQ 时，后者拿到 409 而不是覆盖前者的工作
- 每版**完整快照**，词表日后改名也不污染历史
- **一条主线只有一个 current** —— 数据库部分唯一索引在守，不靠应用自觉

### 6.5 还缺什么

| 缺的 | 说明 |
|---|---|
| ~~版本时刻的内容复原~~ | ✅ **已做**（2026-09-16）：`domain/feature_history.py` 沿 `feature_changes` 反向回放。**顺带修好了 `/diff` 的 `modified` 恒空** —— 它此前用同一条 SQL 取 diff 两端、两边都是当前值，所以 `before != after` 永远为假 |
| ~~版本链可视化~~ | ✅ **已做**（2026-09-16，F 批）：溯源时间轴 + 每版来源链 + diff 版本选择。⚠️ **方案原本要的「合流虚线」没做也不该做** —— 见下方说明 |
| **多个候选标题** | 一条需求只有一个标题；「同一需求的不同视角入口」不存在（**后端已做、前端未接、数据 0 行**） |
| **文档版本链** | `document_asset` 只有 checksum 去重，**没有版本概念**；同名同格式文档改一个字会变成两个互不相干的资产。**方案已写：`docs/方案_文档版本链.md`**（批 1–2 已落地，写入路径未动） |
| ~~主线判定建议~~ | ✅ **已做**（`a85ee9f`）：分析输出 `suggestion{action,target,confidence,reason}`，**只出建议、人工确认** |
| **行内高亮** | diff 只到「功能条目」粒度，没有内容片段级对比 |

**未验证**：详情页的「能力与条件」「当前功能明细」「需求关系」几个区块**仍未在浏览器里点过**
（「版本历史」与「版本 Diff」两个区块已由 F 批用 CDP 验过，见 §五）。
**回滚端点没有前端入口**（`/revert` 只有后端 —— → §二 B7）。

> ⚠️ **为什么没有「版本合流」这个概念。** 方案 §3.3 原计划加 `merged_from_*` 两列，
> 记「这次版本并入了**另一个 REQ** 的哪一版」。实测那个前提不成立 ——
> 本系统的合并是 **「来源 → REQ」**：待合并的东西是一条 `requirement_source`（还在待审），
> **它还没有 `requirement_key`**，所以 `diff_payload.target_requirement_key` **永远等于
> 版本自己所属的 REQ**，那两列会恒等于自指、画出来就是实线的重复。
> 真实存在的跨实体关系是「**版本 ← 来源**」（`requirement_version_source`），
> 时间轴画的就是它。详见 `docs/方案_对话状态机与Git式版本管理.md` §3.3(a)。

---

## 七、文档导航

| 文档 | 用途 |
|---|---|
| `docs/current-state.md`（本文） | 真实进度 + **§二 统一的待办任务清单** —— **先看这个** |
| `docs/方案_需求主线与能力模型.md` | **能力 / 条件 / 需求主线**的设计方案：现状分析、现有模型映射、迁移方案、8 条设计歧义（**§9 的 4 条卡住 schema，待拍板**） |
| `docs/方案_对话状态机与Git式版本管理.md` | 对话状态机/并发隔离/Git 版本管理的**完整设计方案与批次**（**A–G 已全部落地**）。⚠️ §3.3 的 `merged_from` 前提不成立，已在该节标注替代做法 |
| `docs/History/需求规格.md` | 需求规格原稿 |
| `docs/History/数据模型与实施史.md` | 数据模型的演进与实施记录 |
| `docs/History/工程史附录.md` | 工程史附录 |
| `docs/方案_Agent工具层.md` | **Agent 工具层**的方案（**未动工**）：现状、三个必答问题、契约与注册表、第一批 10 个工具、三层防护、分三批、4 条待拍板 |
| `docs/方案_文档版本链.md` | **文档侧版本链**的方案：现状、三个卡点、分批、4 条待拍板。**批 1–2 已落地**（数据层 + 分片哈希），**批 3–5 未动**（改上传路径，有风险 → §二 B8） |
| `docs/分析_工具分层现状与越层调用.md` | **工具分层的现状盘点** —— 四层映射、越层调用清单（路由直调仓储 86 处 / 仓储默认自建 session 43 处）、与「严格四层」规则的逐条比对。**动工具层之前先看这个** |
| `docs/流程_需求从提交到入库.md` | **一条需求从提交到入库的完整链路** —— 谁做什么、模型在哪、人在哪、每次写入落在哪张表。**想搞清「为什么模型不写库」先看这个** |
| `docs/api-contract.md` | **前端接口契约** —— 每个端点的字段名与形状（实测核对过）。**重写前端前先看这个**；§10 是还没做完的清单 |
| `scripts/verify_api_contract.py` | **契约验证脚本** —— 逐端点核对字段 + 清点大整数 JSON 类型 + 查库找精度风险。改契约就要同步改它的 `SPEC` |
| `README.md` | 环境搭建、常用接口、目录结构 |
| `docs/refactoring/archive/*` | 阶段 0/1 施工快照与决策依据（**进度信息已过时**） |
| `migrations/README.md` | 迁移清单 + 向量维度与索引的决策 |
