# 前端重构实施记录

> 2026-10-12 全功能验收：新隔离库已按 migrations 完整迁移；全量 pytest 暴露多处历史测试
> 对开发库种子的依赖，未以 skip 掩盖。详情见 `docs/full_function_test_report.md`。

> 2026-10-12 隔离复测：`/app`、`/app-next` 和 `/app-next` 深链接均由本地隔离 FastAPI 返回
> 200，未授权 API 返回 401；Vitest 9 passed、typecheck/build 通过。桌面/390px 截图见
> `docs/design/screenshots/2026-10-12-audit-app-next-*.png`。这不是八页面有数据全流程通过：
> 真实数据夹具仍待自包含化，详见全功能报告。

## 业务流程正确性修复（2026-10-12）

- 审核 `source_id` 已从路由、前端请求体到后端 schema 保持**字符串**；删除
  `ReviewPages.tsx` 中的 `Number(sourceId)`，schema 拒绝 JSON number，只在 Python 领域层转换。
  回归样本为 `9007199254740993123`，大于 `Number.MAX_SAFE_INTEGER`，请求 JSON 逐字保留。
- 409 现在是显式的裁决锁：批准、拒绝、退回全部禁用；必须点击「重新获取」并完成详情（有目标
  时还包括预演）后才解锁。查询失效/后台刷新不会静默清除确认。非 `pending_review` 历史来源只读，
  不渲染裁决区。
- assistant SSE 对可处理 provider 异常统一持久化 `failed`，并发明确 `error` +
  `done.status=failed`；完成流也发 `done.status=completed`。预算耗尽维持持久状态
  `failed + meta.outcome=incomplete` 与流 `incomplete`，暂停、取消、澄清分别保留
  `paused`、`cancelled`、`waiting_clarification`。协程 `CancelledError` 不再被吞掉。
- 新增 Vitest 最小配置与 9 个前端协议/审核规则测试：分块、CRLF、多行 data、尾部截断、
  artifacts 先于 narrative、incomplete、精度、409 锁、重复提交与历史只读。新增 Python
  回归覆盖 schema/路由精度、provider 网络异常终态与协程取消。

本轮验证：`npm run test`（9 passed）、`npm run typecheck`、`npm run build`；
`.venv/bin/python -m pytest -q tests/unit/test_review_request_contract.py tests/unit/test_agent_chat_narrative.py`
（12 passed）。尝试在隔离库 `req_agent_iso` 跑聊天生命周期、澄清与审核提交集成组时，宿主
PostgreSQL TCP 要求未提供的密码，测试在首个连接前失败、没有写入任何数据；此项待提供该隔离库
凭据或容器内测试入口后重跑。真实 LLM/Tavily 未联调（未使用或输出任何密钥），以上聊天测试均为
确定性假服务。

## 隔离库端到端复验与终态事件修复（2026-10-12）

- 修复动态聊天收尾顺序：`complete_chat_message` 已原子落库最终事件、状态和 assistant 消息后，
  不能再经 `append_events_if_owned(... status='running')` 发送 `done`；现将终态 SSE 改为只通知
  明确状态。澄清、成功、失败、暂停、取消和预算耗尽均保持一致。
- 确认隔离库 `requirement_agent_codex_isolated` 与专用非超级用户测试入口；临时认证仅用于本轮，
  验证后已撤销。未连接 `requirement_agent` 业务库，也未输出业务或第三方凭据。
- 隔离库聊天预算/暂停/澄清、审核提交、合并预演及动态 checkpoint 组：**21 passed**。覆盖确定性
  fake 模型的问答成功、预算耗尽、暂停、澄清补充、预演、批准和版本；测试数据均由用例清理。
- 终态修复后扩大回归至聊天生命周期、动态 checkpoint、run tracking、resume、审核提交、预演和
  工作台端点：**55 passed、1 skipped、2 failed**。两个失败来自
  `test_workbench_endpoints.py` 假设空隔离库已有 `committed` 来源；它们不是认证或业务回归，
  但测试应改为自行建/清理该数据，尚未将其掩盖为通过。
- `POST /reviews/submit` 全部调用方复查完毕：React、旧版页面和集成测试均传字符串；历史 curl 示例
  也改为带引号的字符串。大 ID 精度测试保留。

## F3–F6 与 F1 遗留项（2026-10-10，第二次修正轮）

对照方案 §8，本轮补齐了 **F1 实际空缺的两项**，并完成 **F3 / F4 / F5**，F6 见文末。

### 0. 先纠正一个进度错觉

方案把「HTTP/**SSE** 层」和「统一设计变量」都算在 F1，但两项此前**都不存在**：
`lib/http.ts` 只有一个总是 `response.json()` 的 `api()`，全仓库没有任何流式客户端；
`styles/index.css` 一个 CSS 变量都没有。所以 F1 并未真正完成，本轮先补这两项。

### A. F1 遗留项

- **SSE 客户端** `frontend/src/lib/stream.ts`。后端是 POST + SSE 且要 `Authorization`，
  `EventSource` 不能用；也不能复用 `api()`。按真实帧格式实现（`id:` 仅 step/done/error 带、
  data 多行合并、CRLF 容错、注释与未知字段忽略、尾块丢弃、409 的 `active_run` 解析）。
  **读取消与写取消分开**：`abortRead` 只关响应，真正停止要打 `POST /agent/runs/{id}/cancel`；
  卸载只断读、绝不取消服务端 run。**不做**自动重连与 `Last-Event-ID` 回放（该契约不存在）。
- **设计变量** `--ra-*`。取值逐字保持原样，**不改视觉结果**；抽取后重出资料库 / 需求详情 /
  文档详情三张截图与提交版本**逐字节一致**（`cmp` 验证）。

### B. F3 对话页（完成）

- 状态机 `features/chat/reducer.ts` 处理三处反直觉：① `done` 经常**不带 status**，
  用 `sawErrorFrame` 区分成功与失败收尾；② `artifacts` 在分析管线上**先于** `narrative{start}`
  到达，气泡不能靠 start 创建；③ `incomplete` **不是成功**（琥珀色）。
- 正文用 `react-markdown` + `remark-gfm`：输出 React 元素、**无 innerHTML**、不启用 `rehype-raw`、
  链接白名单 + `noopener noreferrer nofollow`；懒加载成独立 chunk（158 kB 不进首屏）。
- `artifacts` 按**真实形状**渲染（旧版用 `Array.isArray` 判对象，恒假 → 从未渲染过）。
- 模式/联网/来源在**带附件时置灰**（`stream-with-files` 没有这三个参数）；
  没有文档范围参数，故不做文档选择器，只给「在问题里写 REQ 编号」的提示。
- 侧栏「最近对话」改跳站内 `/chat/:sessionId`，保留「旧版对话」次级入口。

### C. F4 审核（完成）

列表（待审 / 历史）+ 详情三栏（原始材料 / 拟变更 / 来源与风险）+ 底部裁决区。
增改删用淡绿/淡黄/淡红**并保留文字标记**。**预演失败即禁用批准**；任何 409 都**不自动重试**。

### D. F5 任务记录（完成）

`failed` + `meta.outcome=incomplete` 映射为**未完成（预算或超时耗尽）**（琥珀色），原始状态
小字保留供诊断。重试只对分析类 run 出现（后端对会话 run 会拒绝）。普通视图不显示租约字段。

### E. F6 验收（本轮范围内完成）

**实测（隔离库 `req_agent_iso` + 真实 FastAPI + 真实 Chrome；业务库零写入）：**

| 项 | 结果 |
|---|---|
| `/app-next`、哈希资源、`ra-ui-token` 注入、深链接硬刷新、旧 `/app` | 全部 200 / 正确注入 |
| 对话发送「你好」 | `session/step/narrative/done` 正确解析，`done` 无 status 判为成功 |
| ask_library 一轮 | 服务端 provider 401 后流中断 → 界面显示「未正常结束」，未被当成成功 |
| 需求列表 `module_names` | 报表、订单 / 支付 / **未提供** |
| `/trace` | v2 `meeting·李娜`、v1 `web·张伟`，含摘要与时间 |
| 文档详情 / 分片 / 无版本流 404 | 均按真实响应渲染，404 为正常态不崩 |
| 审核列表 / 详情 / 合并预演 | 真实待审来源；选 REQ-000101 预演返回「v2 → v3；未变更 3 项」+ 1 条新增 |
| 任务列表 / 详情 / 未完成映射 | 真实 run 行与事件行；incomplete 渲染为琥珀色 |
| 窄屏 390px | 四个页面均无横向溢出；本期修掉审核/任务列表在窄屏保留 5 列栅格导致标题竖排的问题 |

截图 20 张存 `docs/design/screenshots/`（8 页桌面 + 对话/资料库/审核/任务窄屏 + 版本切换 +
旧版来源入口），逐张对照 `docs/design/requirement-workbench-reference.png`。

接口映射见 **`docs/frontend_api_mapping.md`**（当前版；`docs/refactoring/frontend-api-mapping.md`
仍是 2026-09-23 的历史盘点，不要当现状用）。

### 仍未完成 / 阻塞

- **真实 LLM / Tavily / MCP 未联调**：本机 `.env` 的 DeepSeek key 实测返回 **401**，
  因此 F3 只能验证到「协议解析 + 失败呈现」，**没有产出过真实模型答案**。
  可验证的路径（casual / 截断 / 取消）都验了；完整的 ReAct 工具调用链未能跑通。
- **后端缺陷（未在本批修复，属后端范围）**：`agent_chat.py:711` 的 assistant 流只捕获
  `(RuntimeError, ValueError)`，provider 抛的 `httpx.HTTPStatusError` 会穿透 →
  **流不发终态帧就关闭**，run 留在 `running` 直到租约过期。前端已按「未正常结束」处理；
  后端应放宽 except 并补终态帧。
- 澄清的持久恢复、断线回放、SSE 后台化仍未实现（后端无对应契约），前端据实说明。
- 参考图的「…」行内操作列、模型调用明细区未做（无真实接口，不放假按钮/假数据）。


## F0–F2 修正轮（2026-10-10，真实数据库联调）

本次把 F0–F2 的「构建通过」推进到「对真实后端验证」，并修掉一个业务字段错误。

### 1. 所属模块（业务 bug，已修）

- `frontend/src/features/library/LibraryPages.tsx` 的 `Requirements()` 原先把 `business_domains.join("、") || business_domain` 显示在「所属模块」列下 —— 那是**业务域**，不是模块。
- 改用后端真实的 `module_names`（来自 active `requirement_feature.module_name`，migration 014）。空数组显示显式 `未提供`，绝不用 `business_domain` 顶替。
- `frontend/src/contracts/api.ts` 的 `Requirement` 增加 `module_names: string[]`。
- 实测（隔离库 `req_agent_iso`）：REQ-000101 → 报表、订单；REQ-000102 → 支付；REQ-000103 → 未提供。

### 2. /trace 契约按真实结构重写

- 原 `RequirementTrace` 是**臆造的**（`{requirement_key, versions:[{version_no, source_id, source_type}]}`），与服务器不符。
- 已按真实响应重写：`{requirement, versions:[{version_id, version_no, ..., sources:[...]}]}`，新增 `TraceSource` / `TraceVersion` / `TraceRequirement`。
- 「来源证据」面板现在按版本列出每个真实来源：来源类型、提交人（`requester_name`）、`original_text` 截断摘要；每条来源链接到**真实可用的**旧审核入口 `/app/reviews?source_id=<source_id>`（`static/app/js/pages/reviews.js` 读取该参数并打开审核抽屉）。不编造页码或引文。
- 该入口最初**是坏的**：旧抽屉对每一条来源都报 `加载失败 Failed to execute 'appendChild' on 'Node': parameter 1 is not of type 'Node'`，等于「链接能到、详情区永远报错」，不满足「实际可用的来源入口」。**已修复**（编排 Agent 接手，属旧版代码，不在 frontend/** 内），见 §7。

### 3. 上传反馈与去重

- `UploadButton` 现在有：进行中（禁用 + 文案「上传中…」）、成功、失败（显示真实 `detail`）三态，且上传在途时 input `disabled`，阻止重复提交。
- 成功文案遵守真实语义：`status==="queued"` → 「已接收排队（queued），索引尚未就绪」，`"ready"` → 「已入库，索引就绪」。上传成功后刷新 `["library","documents"]` 与 `["recent-documents"]`。
- 实测（CDP 驱动真实 Chrome 点击真实 input）：上传后 `.upload-note` 文案正确，文档行数 4 → 5，新文件置顶；限速网络下在途时 `input.disabled === true`，第二次投递**未产生第二个 POST**（`uploads_total: 1`）。

### 4. 响应类型与边界

- `frontend/src/contracts/api.ts` 全面对齐真实端点：`DocumentAsset`/`Version`/`RequirementFeature`（含 `module_key`/`module_name`）/`RequirementDiff`/`TraceSource`；`Id` 全程字符串（雪花 ID 不转 number）。
- `frontend/src/lib/http.ts`：错误对象带上服务端 `detail` 与 `request_id`；`NotFoundError` 单独类型；`listItems` 接受 `undefined`。
- 文档详情新增真实「版本流」区：`/api/v1/documents/{id}/versions` 返回 404 时显示「该文档没有版本流（接口返回 404）」，不崩溃。
- Diff 面板在无变更时显示「两个版本之间没有功能变更」，不伪造增删。

### 5. 真实联调证据（FastAPI :8124 + 隔离库 `req_agent_iso`）

- `/app-next` 200，HTML 内注入 `ra-ui-token`，哈希资源 `/app-next/assets/*.css|js` 均 200；深链接 `…/library/requirements/REQ-000101` 直接刷新 200；旧 `/app`、`/app/reviews`、`/app/reviews/detail` 均 200。
- 需求列表 / 版本 / 功能 / trace / diff、文档列表 / 详情 / 分片均返回真实数据；`/api/v1/reviews/{source_id}/detail` 对已入库来源 200。
- 版本选择器真实改变请求：默认下发 `diff?to_version=2`（v1 → v2），选 v1 后变为 `diff?to_version=1`（v1 → v1），截图为证。

### 6. 截图（docs/design/screenshots/）

`library-documents-desktop.png`、`library-requirements-desktop.png`、`requirement-detail-desktop.png`、`document-detail-desktop.png`、`requirement-detail-version-picker-v1.png`、`library-narrow-390.png`、`requirement-detail-narrow-390.png`、`legacy-reviews-source-entry.png`（旧审核入口抽屉，**修复后**的实际渲染）。

### 7. 旧版审核抽屉渲染缺陷（已修，编排 Agent 接手）

来源入口的目标页面本身是坏的，两个独立原因，都在 `static/app/js/pages/reviews.js`：

1. `section(title, children)` 用 `[el('h3', ...), children]` 拼子节点，而 `core.js` 的 `el()` 内部只做**一层** `[].concat()` 展开 —— 传数组时得到的是「数组当节点」，`appendChild` 直接抛错。改为 `[el('h3', ...)].concat(children)`。这正是「结构化需求」「AI 分析结果」两节传数组的写法，所以此前每条来源都打不开。
2. 降级标记是条件渲染（`degradation ? el(...) : null`），裸 `null` 进了 `body.replaceChildren(...)`，而它不接受 null。按 `el()` 同样的规则过滤空位。

验证方式：真实 Chrome 跑到异步渲染完成再 dump DOM。修复前抽屉只有一个「加载失败」；修复后 8 个区块全部渲染（来源事实 / 原始输入 / 结构化需求 / AI 分析结果 / 能力匹配 / 相似与冲突候选 / 人工裁决），显示真实来源 ID、提交人「张伟」、渠道 web、状态「已入库」。

### 视觉对齐（对照参考图）

- 改：上传按钮从页头移到标签行右侧、与标签共用一条基线（参考图版式）；搜索占位文案改为「搜索文件名、内容或关键字…」；新增来源卡片、模块标签、版本证据块的间距与配色（沿用现有 token）。
- **未改**：整体配色、侧栏 248px、字号层级、卡片圆角、表格密度 —— 与参考图一致，故不动。参考图中的「…」更多操作列未实现（无真实接口，不放假按钮）。

### 未完成 / 阻塞

- F3 对话、F4 审核、F5 任务记录、F6 全量视觉验收仍未开始（本轮范围只有 F0–F2；是否进入 F3 待 F0–F2 验收结论）。
- 旧审核抽屉渲染缺陷已在 §7 修复并验证，不再是阻塞项。
- 参考图中的「…」行内更多操作列仍未实现（无真实接口，不放假按钮）；文档版本链只有在该文档存在 `stream_id` 时才有，种子文档没有，界面如实显示 404 态。
- 上传的成功/失败 UI 均用 CDP 真实驱动：空文件触发后端 400 `uploaded file is empty`，界面显示「上传失败：uploaded file is empty」（bad 态），非通用兜底文案。

## F0–F2（2026-10-10）

- 已完成：在 `frontend/` 新建 React + TypeScript strict + Vite 工程，使用 React Router、TanStack Query 与 Lucide；`/app-next` 的路由与 `/app` 旧入口分离。
- 已完成：GPT 风格的共享资料库壳层。一级入口保留新对话、资料库、待审核、任务记录；后两项及聊天在本批显式跳转旧页，未假装接入不稳定的恢复、审核或 SSE。
- 已完成：资料库文档/正式需求标签，严格使用实际 `file_name`、`requirement_name` 等字段；ID 在契约与 URL 中均为字符串，已移除宽泛 `UnknownRecord`。文档详情读取详情/分片；需求详情读取版本、功能、结构化 Diff、结构化来源。版本选择会实际改变 features/Diff 请求。
- 已完成：上传资料调用既有 `POST /api/v1/documents/upload`，成功后刷新文档窗口；最近新增合并真实文档/需求窗口，最近对话读 `GET /api/v1/conversations`，无数据时显示空状态，侧栏折叠有真实状态行为。
- 已完成：FastAPI 以 `/app-next` 提供构建结果、`/app-next/assets` 提供哈希资源，所有非资源子路径回退至 SPA index，继续使用运行时注入的 `ra-ui-token`。`/app` 仍是旧版回退入口。
- 搜索是当前已加载窗口内过滤，UI 显示窗口条数，不标为全库搜索或分页。资料范围问答尚无结构化后端支持，详情页明确说明不能保证限定检索，只链接旧入口。
- 真实接口缺口：文档列表只接受 `limit`，需求列表没有本批前端实际用到的总数/分页契约；因此 UI 不显示虚构 total。文档版本 API 已存在但本轮详情优先实现正文/片段。
- 验证：`npm run typecheck`、`npm run build`、FastAPI 模块编译、`git diff --check` 已通过。隔离库 FastAPI 启动联调仍待完成；不把构建验证误报为真实 API/上传验收。

## 未开始

- F3 对话、F4 审核、F5 任务记录、F6 全量视觉验收。
- 与隔离数据库的文档、需求详情、上传真实路径及带真实数据截图；不在业务库执行。
