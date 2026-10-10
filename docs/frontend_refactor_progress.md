# 前端重构实施记录

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
- **诚实披露（未修复，属旧代码）**：该旧抽屉在打开时会报 `加载失败 Failed to execute 'appendChild'...` —— 这是 `static/app/js/pages/reviews.js` 的既有缺陷（`body.replaceChildren(...)` 里传入了 `null`），**不在本次 frontend/** 所有权范围内，未改动。链接本身不是死链：它确实导航到审核中心、打开该来源抽屉，并在「审核历史」中渲染该来源行。

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

`library-documents-desktop.png`、`library-requirements-desktop.png`、`requirement-detail-desktop.png`、`document-detail-desktop.png`、`requirement-detail-version-picker-v1.png`、`library-narrow-390.png`、`requirement-detail-narrow-390.png`、`legacy-reviews-source-entry.png`（旧审核入口打开抽屉的失败实况）。

### 视觉对齐（对照参考图）

- 改：上传按钮从页头移到标签行右侧、与标签共用一条基线（参考图版式）；搜索占位文案改为「搜索文件名、内容或关键字…」；新增来源卡片、模块标签、版本证据块的间距与配色（沿用现有 token）。
- **未改**：整体配色、侧栏 248px、字号层级、卡片圆角、表格密度 —— 与参考图一致，故不动。参考图中的「…」更多操作列未实现（无真实接口，不放假按钮）。

### 未完成 / 阻塞

- F3 对话、F4 审核、F5 任务记录、F6 全量视觉验收仍未开始（本轮范围只有 F0–F2）。
- 旧审核抽屉渲染缺陷（见 §2）不是本轮前端可修范围内的项。
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
