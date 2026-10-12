# 当前前端接口映射（/app-next，F0–F6）

方案 §2 要求「更新一份**当前**页面→接口→字段→状态→权限映射，记录验证方式与缺口」。
`docs/refactoring/frontend-api-mapping.md` 是 **2026-09-23** 的历史盘点，不能当现状用；
本文件是**新工作台**（`frontend/` → `/app-next`）的当前映射，逐条对过代码与运行中的服务。

验证环境：隔离库 `req_agent_iso`（3 文档 / 3 需求 / 4 版本 / 5 个带模块的功能 +
1 条 `pending_review` 来源）+ 本地 FastAPI（`POSTGRES_DB=req_agent_iso`）。
**业务库 `requirement_agent` 全程未被执行任何写操作。**

## 权限

所有 API 都要 `Authorization: Bearer <token>`，token 由服务端在响应 HTML 里以
`<meta name="ra-ui-token">` 注入（CSP 禁内联脚本，故不能写 `window.*`）。
`api/auth.py` 分档：**GET 一律 `read`**；`POST/PUT/PATCH/DELETE` 落到
`^/api/v1/agent/` 与 `^/api/v1/conversations` 的走 `ANALYZE`（三种角色都有）。
`/app-next/` 本身在豁免列表里，但页面内每个 API 调用都必须带 token。

## 8 个主页面

| # | 页面 | 路由 | 主要接口 | 关键字段 | 权限 | 状态 |
|---|---|---|---|---|---|---|
| 1 | 对话 | `/app-next` `/app-next/chat/:sessionId` | `POST /api/v1/agent/chat/stream`（JSON）、`POST .../stream-with-files`（multipart）、`GET /api/v1/agent/chat/{session_id}`、`GET .../resumable`、`POST /api/v1/agent/runs/{id}/cancel\|pause\|resume` | SSE 事件 `session/step/narrative/artifacts/done/error`；`mode`(auto/ask_library/draft_requirement/research)、`web_policy`（默认 `auto`）、`client_message_id` | ANALYZE / READ | 完成 |
| 2 | 资料库 | `/app-next/library?tab=documents\|requirements` | `GET /api/v1/documents?limit=`（≤50）、`GET /api/v1/requirements?limit=`（≤500）、`POST /api/v1/documents/upload` | 文档 `file_name/content_type/status/created_at`；需求 `requirement_key/requirement_name/module_names/current_version/latest_source_submitted_at` | READ / ANALYZE | 完成 |
| 3 | 文档详情 | `/app-next/library/documents/:documentId` | `GET /api/v1/documents/{id}`、`/{id}/chunks`、`/{id}/versions` | `extracted_text/original_text/version_no/size_bytes` | READ | 完成 |
| 4 | 需求详情 | `/app-next/library/requirements/:requirementKey` | `GET .../versions`、`.../features?at_version=`、`.../diff?to_version=`、`.../trace` | 功能含 `module_key/module_name`；trace = `{requirement, versions[].sources[]}` | READ | 完成 |
| 5 | 待审核 | `/app-next/reviews?tab=pending\|history` | `GET /api/v1/reviews/pending?limit=`、`GET /api/v1/reviews/history?limit=` | `source_id`(**字符串**)`/source_type/requester_name/metadata.extracted.requirement_title/metadata.analysis.suggestion.action/processing_status/updated_at` | READ | 完成 |
| 6 | 审核详情 | `/app-next/reviews/:sourceId` | `GET /api/v1/reviews/{source_id}/detail`、`.../merge-preview?target_requirement_key=&merge_mode=`、`POST /api/v1/reviews/submit` | 预演 `groups[].added/modified/deleted`、`summary`、`overrides`、`warnings`、`next_version` | READ / ANALYZE | 完成 |
| 7 | 任务记录 | `/app-next/runs?type=&status=` | `GET /api/v1/agent/runs?status=&run_type=&limit=` | `run_id/run_type/status/meta.outcome/created_at/started_at/ended_at` | READ | 完成 |
| 8 | 任务详情 | `/app-next/runs/:runId` | `GET /api/v1/agent/runs/{id}`、`/{id}/events?after_seq=`、`/{id}/invocations` | 事件 `sequence/event_type/node/payload/created_at`；工具 `tool_name/status/elapsed_ms/error_message` | READ / ANALYZE | 完成 |

## 容易踩的契约点（都已在前端显式处理）

1. **雪花 ID 一律字符串**。`Id = string` 贯穿契约与 URL；`/api/v1/documents/{id}` 是例外 ——
   路径参数是 **int**，前端保持字符串再 `encodeURIComponent`。
2. **新执行流的 `done` 一律带 `status`**（`completed` / `failed` / `incomplete` /
   `paused` / `cancelled` / `waiting_clarification`）；前端仍以「是否见过 `error` 帧」兜底
   兼容历史 run 或代理截断，不能把缺 status 当作新的正常协议。
3. **联网默认 `auto`，不是无条件联网**。只有消息被识别为外部调研（例如“联网查询”
   “搜索网页”），或用户把联网切到“联网开启”时，才会向 Tavily MCP 开放只读工具。
   成功结果在回答下方以可点击的“相关资料来源”展示；“本轮实际工具调用”只列真实调用，
   不会把未调用或配置失败说成已联网。
4. **`artifacts` 是自由 dict，形状随路径变**。按存在的键判别：含 `extracted|candidates|analysis|risk`
   → 分析管线；含 `sources|tool_calls|incomplete_reason` → 助手；`{intent,answer_mode}` → 闲聊。
   旧版 `static/app/js/pages/chat.js` 用 `Array.isArray` 判它，恒为假 —— 结构化结果从未渲染过。
5. **`failed` + `meta.outcome=incomplete` = 未完成（预算/超时）**，不是普通失败，也不是成功。
6. **`source_id` 在所有端点（含 `POST /reviews/submit` 请求体）都是字符串**。前端不做
   `Number()` / `parseInt()`；后端 schema 强制 JSON string，仅在进入 Python 领域服务时精确转
   为整数，避免超过 `Number.MAX_SAFE_INTEGER` 的 snowflake 改写成另一条来源。
7. **任何 409 都不自动重试**。`merge-preview` 与 `submit` 的 409 含义不同（前者是「来源不在待审」，
   后者是乐观锁冲突「该需求已被他人修改」），都必须重新加载。对话的 409 是**对象** detail
   （带 `active_run`），与 pause/cancel 的字符串 detail 不同。
8. **终态事件只能写一次**。`complete_chat_message` / `fail` 在持有 token 的同一事务内先写
   最终事件和 run 状态；随后 SSE `done` 只是通知，不得再经 `status='running'` 追加接口写入，
   否则正常完成会被误判为失权。

## 诚实记录的能力缺口（界面已如实说明，未伪装）

| 缺口 | 事实 | 界面做法 |
|---|---|---|
| 带附件的模式/联网 | `/chat/stream-with-files` **没有** `mode`/`web_policy`/`source_type`（服务端把 source_type 固定 `web`） | 三个控件置灰 + tooltip，不静默丢弃 |
| 资料范围问答 | 两个 chat 端点都**没有** `document_ids`/scope 参数 | 不做文档选择器，只提示「在问题里写 REQ 编号或文件名」 |
| 澄清的持久恢复 | 澄清只有内联的 `done{status:"waiting_clarification"}`，`resumable` 只报 paused 的 checkpoint | 横幅只在本次会话内有效，刷新后不假装恢复 |
| 模型调用明细 | `/agent/runs/{id}/invocations` 的 `models` **恒为 `[]`** | 工具调用照常展示，并注明模型明细不由该接口提供 |
| 流式断线 | 无 `Last-Event-ID` 回放端点 | 不自动重连；用同一 `client_message_id` 重发（完成态会回放不重算） |
| 服务端异常未发终态帧 | provider 抛非 RuntimeError/ValueError 时流直接断（见下） | 客户端判为「未正常结束」并给出横幅 |
| 列表分页 | 文档/需求列表只有 `limit`，没有 total/offset | UI 显示「已加载 N 条」，不伪造总数 |

## 本次发现的后端缺陷（未在本批修复）

本轮已修复：assistant 流对 provider 鉴权、网络、超时等可处理异常，先以 execution token
持久化 `failed`，再发 `error` 和显式 `done.status="failed"`。`CancelledError` 单独继续抛出，
断连不伪装为用户取消；用户暂停/取消仍由控制接口决定终态。前端仍保留无终态帧的截断兜底，
以兼容旧 run 或代理截断，而不再依赖成功流缺 status 的推断。
