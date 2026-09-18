# 前端接口映射表（frontend-api-mapping）

> **生成日期**：2026-09-18
> **用途**：前端重构的**唯一接口事实源**。每个参数、每个响应字段都要能追到出处。
> **产出方式**：见 §0。**没有一条是凭记忆或推测写的。**

---

## 0. 这份文档怎么产生的（先说方法，再说结论）

沿用契约文档 `docs/api-contract.md` §9 的方法，并补上它没覆盖的部分：

| 证据等级 | 做法 | 覆盖范围 |
|---|---|---|
| **A · 实测** | `TestClient` 打**真实库**，记录 status / 顶层形状 / item 键 / 每个 id 类字段的 JSON 类型 | 全部 **GET** 端点（23 个），见 §4 |
| **B · 源码取证** | 读路由 + Pydantic schema + repository 的 `_normalize_*` / SQL SELECT | 全部端点，**含写端点**（写端点不能在真实库上打） |
| **C · 契约文档** | `docs/api-contract.md`（989 行） | 已记录的 50 个路径 |

三条硬规则（来自本次任务要求 §三）：

1. **不猜**。任何一处拿不到出处的字段，写「未实测」而不是补一个看起来合理的值。
2. **不按中文 `detail` 判错**。前端只按 HTTP 状态码分支。
3. **不伪造默认值**。后端没给的数据，界面上显示「未提供」。

> ⚠️ **OpenAPI 不能当字段事实源。** 实测：73 个端点里 **67 个的响应 schema 是「自由 object」**
> （`resp 200: object(自由)`），FastAPI 拿不到具体字段。照 OpenAPI 写前端字段 = 照空气写。

---

## 1. 全局约定（所有页面共用，写死在 http.js / api.js 里）

| 约定 | 结论 | 出处 |
|---|---|---|
| **前缀** | 一律 `/api/v1`（只有 `/health` 例外，无前缀） | 实测 73 条路由全部如此 |
| **列表形状** | 一律 `{"items": [...]}`，**不是裸数组** | 契约 §1 |
| **鉴权** | 全部受保护端点要 `Authorization: Bearer <token>`；token 由服务端注入 HTML（`window.RA_UI_TOKEN`） | 契约 §1.2 · `app.py:203-231` |
| **错误** | 按 **状态码** 分支：401 / 403 / 404 / 409 / 422。**禁止解析 `detail` 中文文本** | 契约 §1.2 |
| **雪花 ID** | 契约要求**一律 JSON 字符串**，前端永不 `Number()` | 契约 §1.1 |
| **分页** | 全是 `limit`，**没有游标**。每个端点的默认值与上限都不同 —— 见 §2 逐条 | 契约 §5 约定 2 |
| **日期筛选 `to`** | 前端要补 `T23:59:59`，否则当天数据被排除 | 契约 §1 |
| **时间** | 一律走 `as_display_iso`（`+08:00`）。`/agent/runs` 曾是例外，已修（§5.2） | 实测 |

### 1.1 权限档次 → 前端该怎么表现

| 档次 | 覆盖端点 | 前端表现 |
|---|---|---|
| `read` | **全部 GET** | 任何角色可用 |
| `analyze` | `/agent/*`、`/conversations/*`、`/memory*` | 灰掉并说明「需要 analyze 权限」 |
| `submit` | `/requirements/submit`、`/requirements/ingest` | 同上 |
| `review` | `/reviews/submit`、能力/条件/标题/关系的**裁决**端点 | 同上 |
| `revert` | `/requirements/{key}/revert` | **只有 admin**，前端要显式标注 |
| `ops` | `/ops/*`、`/documents/{id}/reindex` | 同上 |

`reviewer` = read+analyze+submit+review；`admin` = 全部；`system_worker` = read+analyze+ops。

> ⚠️ **前端灰按钮不是安全边界。** 它的作用是「别让人点了才报错」，真正的判定在后端 403。
> 403 响应体里的 `required_scope` 字段会告诉你**缺哪一档** —— 提示文案可以用它，但**分支判断仍走状态码**。

---

## 2. 页面 → 端点映射总表

按实施顺序排列（与 `docs/方案_前端工作台.md` §七 的 F 批次一致）。

就绪度图例：**✅ 实测通过** · **🔶 仅源码取证**（写端点，未在真实库上执行） · **⛔ 无后端支撑，不接入**

### 2.1 阶段 3-5 · 地基（无页面，被所有页面依赖）

| 端点 | 用途 | 档次 | 就绪 |
|---|---|---|---|
| `GET /api/v1/health/db` | 全局健康 | **豁免** | ✅ |
| `GET /api/v1/health/llm` | 模型配置 | **豁免** | ✅ |
| `GET /api/v1/health/embedding` | 向量来源可信度 | **豁免** | ✅ |

> 三个 health 端点**不需要 token**（`auth.py:94` 的 `/api/v1/health` 前缀豁免）。
> 前端仍然带上 token 无害，但**不要**把它们的 401 当成「鉴权坏了」。

### 2.2 阶段 6 · 审核中心

| 页面 | 端点 | 档次 | 就绪 |
|---|---|---|---|
| 待审队列 | `GET /api/v1/reviews/pending?limit=` | read | ✅ |
| 审核工作区 | `GET /api/v1/reviews/{source_id}/detail` | read | ✅ 形状同 pending 单项 |
| 合并预演 | `GET /api/v1/reviews/{source_id}/merge-preview?target_requirement_key=&merge_mode=` | read | 🔶 |
| 提交裁决 | `POST /api/v1/reviews/submit` | review | 🔶 |
| 审核历史 | `GET /api/v1/reviews/history?status=&limit=` | read | ✅ |

### 2.3 阶段 7 · 需求工作台 + 需求详情

| 页面 | 端点 | 档次 | 就绪 |
|---|---|---|---|
| 需求库列表 | `GET /api/v1/requirements?…` | read | ✅ |
| 导出 | `GET /api/v1/requirements/export?…` | read | ✅（CSV） |
| 全文检索 | `GET /api/v1/requirements/search?q=…` | read | ✅ |
| 详情 · 版本 | `GET /api/v1/requirements/{key}/versions` | read | ✅（样本 REQ-000015） |
| 详情 · 功能明细 | `GET /api/v1/requirements/{key}/features?at_version=&include_deleted=` | read | ✅ |
| 详情 · Diff | `GET /api/v1/requirements/{key}/diff?from_version=&to_version=` | read | ✅ |
| 详情 · 溯源 | `GET /api/v1/requirements/{key}/trace` | read | ✅ |
| 详情 · 关系 | `GET /api/v1/requirements/{key}/relations` | read | ✅ |
| 详情 · 能力条件 | `GET /api/v1/requirements/{key}/capabilities` | read | ✅ |
| 详情 · 候选标题 | `GET /api/v1/requirements/{key}/titles` | read | ✅（形状实测，见 §3.9） |
| 关系裁决 | `PATCH /api/v1/requirements/relations/{relation_id}` | review | 🔶 |
| **影响分析** | —— | —— | **⛔ 后端 B4 未做** |

### 2.4 阶段 8 · 智能分析

| 页面 | 端点 | 档次 | 就绪 |
|---|---|---|---|
| 分析任务列表 | `GET /api/v1/agent/runs?source_id=&status=&run_type=&limit=` | read | ✅ ⚠️ 三参数**不能组合**，见 §3.10 |
| Run 详情 | `GET /api/v1/agent/runs/{run_id}` | read | ✅ ⚠️ **字段集不同，见 §5.3** |
| 节点时间线 / 回放 | `GET /api/v1/agent/runs/{run_id}/events?after_seq=&limit=` | read | ✅ |
| 工具与模型调用 | `GET /api/v1/agent/runs/{run_id}/invocations` | read | ✅ ⚠️ `models` 恒空，模型走 `/ops/models?run_id=`（§3.17） |
| 重跑失败分析 | `POST /api/v1/agent/runs/{run_id}/retry` | **analyze** | 🔶 |
| 渠道分析（单条来源） | `POST /api/v1/agent/run` | analyze | 🔶 ⚠️ **同步执行、会写库** |
| 触发分析（异步任务） | `POST /api/v1/requirements/submit` | submit | 🔶 |

### 2.5 阶段 9 · 输入中心

| 页面 | 端点 | 档次 | 就绪 |
|---|---|---|---|
| 文本提交 | `POST /api/v1/requirements/submit` | submit | 🔶 |
| 文件导入 | `POST /api/v1/requirements/ingest`（**multipart**） | submit | 🔶 |
| 导入结果查看 | `GET /api/v1/sources?…` | read | ✅ |
| 单条来源回放 | `GET /api/v1/sources/{source_id}/trace` | read | ✅ |
| **截图上传** | —— | —— | **⛔ 视觉模型未接** |
| **导入任务进度** | —— | —— | **⛔ 无「按来源聚合的任务状态」端点** |

### 2.6 阶段 10 · 版本与变更

| 页面 | 端点 | 档次 | 就绪 |
|---|---|---|---|
| 时间线 | `GET /api/v1/requirements/{key}/versions` + `/trace` | read | ✅ |
| Diff | `GET /api/v1/requirements/{key}/diff` | read | ✅ |
| 回滚 | `POST /api/v1/requirements/{key}/revert` | **revert（仅 admin）** | 🔶 |

### 2.7 阶段 11 · 来源与知识

| 页面 | 端点 | 档次 | 就绪 |
|---|---|---|---|
| 来源列表 | `GET /api/v1/sources?status=&source_type=&requester=&limit=` | read | ✅ |
| 来源回放 | `GET /api/v1/sources/{source_id}/trace` | read | 🔶 |
| 文档库 | `GET /api/v1/documents?limit=` | read | ✅ |
| 文档详情 | `GET /api/v1/documents/{document_id}` | read | ✅ |
| 文档分片 | `GET /api/v1/documents/{document_id}/chunks?limit=` | read | ✅ |
| 分片检索 | `GET /api/v1/documents/search?q=&limit=` | read | ✅ |
| 重建索引 | `POST /api/v1/documents/{document_id}/reindex` | **ops** | 🔶 |
| 能力词表 | `GET /api/v1/capabilities?status=&q=&limit=` | read | ✅ |
| 条件词表 | `GET /api/v1/constraints?status=&q=&limit=` | read | ✅（形状实测，见 §3.19） |
| 能力→需求反查 | `GET /api/v1/capabilities/{id}/streams?constraint=&review_status=&limit=` | read | 🔶 |
| **文档版本链** | —— | —— | **⛔ 数据层有、HTTP 层没有**（契约 §10-T6） |

### 2.8 阶段 12 · 系统运维

| 页面 | 端点 | 档次 | 就绪 |
|---|---|---|---|
| 健康 | `/health/db`、`/health/llm`、`/health/embedding` | 豁免 | ✅ |
| Outbox / 死信 | `GET /api/v1/ops/outbox` | read | ✅ |
| 死信重投 | `POST /api/v1/ops/outbox/dead-letters/{event_id}/retry` | **ops** | 🔶 |
| 死信丢弃 | `POST /api/v1/ops/outbox/dead-letters/{event_id}/discard` | **ops** | 🔶 |
| Worker | `GET /api/v1/ops/worker` | read | ✅ |
| 模型调用 | `GET /api/v1/ops/models?task_type=&run_id=&limit=` | read | ✅ |
| 审计 | `GET /api/v1/audit/events?limit=` | read | ✅ |
| 对话与记忆 | `GET /api/v1/conversations?limit=&offset=`、`GET /api/v1/memory?…` | read | ✅ |
| **渠道状态** | —— | —— | **⛔ 无端点** |

### 2.9 阶段 13 · 总览 Dashboard

| 区块 | 端点 | 档次 | 就绪 |
|---|---|---|---|
| 全部指标 | `GET /api/v1/stats/overview?trend_periods=` | read | ✅ |
| 系统健康 | 3 个 health 端点 | 豁免 | ✅ |

### 2.10 对话助手（并入，不占一级入口）

| 功能 | 端点 | 档次 | 就绪 |
|---|---|---|---|
| 流式对话 | `POST /api/v1/agent/chat/stream`（**SSE**） | analyze | ✅（6 个事件名见契约 §6） |
| 非流式 | `POST /api/v1/agent/chat` | analyze | 🔶 |
| 多文件 | `POST /api/v1/agent/chat/stream-with-files` | analyze | 🔶 |
| 会话列表 | `GET /api/v1/conversations` | read | ✅ |
| 断点续跑 | `GET /api/v1/agent/chat/{sid}/resumable`、`POST /api/v1/agent/runs/{id}/resume` | analyze | 🔶 |

---

## 3. 逐端点明细

字段表里的每一项都有出处标注：**实测**（打真实库看到）、**契约**（`api-contract.md` §N）、**源码**（`file:line`）。

### 3.1 `GET /api/v1/stats/overview`

**参数**：`trend_periods` int，默认 `12`，`ge=1, le=52`（`routes/stats.py:21`）

**响应**：扁平对象，**没有 `items` 包装**。实测 13 个顶层键：

```
pending_review  pending_total  high_risk  conflict  dead_letter
requirements_total  sources_total  analysed_sources
source_status_counts  channel_counts  domain_counts
risk_matrix  submission_trend
```

| 键 | 形状 | 实测值样例（2026-09-18） |
|---|---|---|
| `pending_review` | int | `3` |
| `pending_total` | int | `3`（= `pending_review` + `dead_letter`） |
| `high_risk` | int | `6` |
| `conflict` | int | `0` |
| `dead_letter` | int | `0` |
| `requirements_total` | int | `4` |
| `sources_total` | int | `12` |
| `analysed_sources` | int | `11` ← **`high_risk`/`conflict` 的分母** |
| `source_status_counts` | `dict[str,int]` | `{"pending_review":3,"rejected":1,"extracting":1,"committed":7}` |
| `channel_counts` | `dict[str,int]` | `{"web":12}` |
| `domain_counts` | `dict[str,int]` | `{"data":1,"未标注":1,"report":2,"workflow":5,"auth":3}` |
| `risk_matrix` | `list[{quality_risk, change_risk, count}]` | `[{"quality_risk":"medium","change_risk":"medium","count":4}, …]` |
| `submission_trend` | `list[{period, count}]`，`period` 形如 `2026-W12`，**时间正序** | 实测为空数组 |

> ⚠️ **`risk_matrix` 里没有 `technical_impact_risk`** —— 尽管 `_RISK_KEYS` 里有它（`stats.py:39`）。
> 前端不要照着「质量×变更×技术影响」三维画图。
>
> ⚠️ **空/None 的键被归一成中文 `"未标注"`**（`stats.py:157-168`）。实测 `domain_counts` 里真的
> 出现了 `"未标注": 1` —— 它不是业务域，是缺值占位。前端可以直接显示，但别把它当业务域选项。

### 3.2 `GET /api/v1/sources`

**参数**：`status`（**可重复、多值**）、`source_type`、`requester`（精确匹配 `requester_name`）、
`limit` int 默认 `50`，`ge=1, le=200`（仓储再夹到 500）

**响应** `{"items":[…]}`，实测 12 条，每项 **9 个键**：

```
source_id(str 实测)  source_type  requester_name  processing_status
error_message  excerpt  linked_requirement_key  submitted_at  updated_at
```

- `excerpt` = `original_text` 前 200 字（`requirement.py:400`）—— 列表**不给全文**、**不给 `metadata`**
- `linked_requirement_key` 为 `null` **不是错误**，是「还没变成需求」
- `error_message` 是失败原因，实测存在该键

### 3.3 `GET /api/v1/reviews/pending` 与 `/{source_id}/detail`

**参数**：`limit` 默认 `20`，`1-100`（契约 §4）

**响应** 实测 3 条，每项 **12 个键**：

```
source_id(str)  source_type  source_event_id  requester_id(str)  requester_name
original_text  extracted_text  original_payload  metadata
processing_status  submitted_at  updated_at
```

**前端要展示的东西几乎都在 `metadata` 里**，且**键是逐个长出来的，不是固定集合**：

| 路径 | 内容 | 实测 |
|---|---|---|
| `metadata.extracted` | `{requirement_title, summary, business_object, requirements[], modules[], raw_text, …}` | ✅ |
| `metadata.analysis` | `{duplicate, related, conflict, independent, reasoning, candidates[], suggestion}` | ✅ |
| `metadata.analysis.suggestion` | `{action, target_requirement_key, confidence, reason}` | **可能整个键不存在** |
| `metadata.risk` | `{quality_risk, change_risk, technical_impact_risk, confidence, source}` | ✅ |
| `metadata.capability_match` | `{business_object, capabilities[], constraints{matched,unmatched}, summary}` | **部分来源才有** |
| `metadata.degradation` | `{degraded, fields[], reason}` | **只有真降级时才存在** |
| `metadata.retrieval` | `{query, recall_limit, candidate_count, contrast, calibration, filters, candidates[]}` | ✅ |
| `metadata.tool_calls` | `[{tool, params, status, duration_ms, count, message, sample}]` | ✅ |
| `metadata.business_domain` | string | ✅ |

> ⚠️ **必须逐键容错**（`(meta.capability_match || {})`），**不要**写 `meta.capability_match.capabilities`
> 这种一步到底的取值 —— 老数据上必炸（契约 §8.2）。实测 4 条待办里没有一个拥有全部键。
>
> ⚠️ **`metadata.retrieval_filters` 已删除**（B4 批 4）。真实状态看 `retrieval.filters`。别写兼容分支。

### 3.4 `GET /api/v1/reviews/history`

**参数**：`status`（**可重复、多值**）、`limit` 默认 `50`，`1-200`
**默认终态**：`["approved","rejected","committed"]` —— **`returned` 被刻意排除**（`reviews.py:128-131`）

**响应**：与 §3.2 `sources` **同一形状**（实测 8 条，9 键，`source_id` 为 string）。

### 3.5 `GET /api/v1/reviews/{source_id}/merge-preview`

**参数**：`target_requirement_key`（**必填**）、`merge_mode`（`union` 默认 / `replace`）

**响应**（契约 §4）：
```json
{"source_id": …, "merge_mode": "union",
 "target": {"requirement_key","requirement_name","current_version"},
 "next_version": 4,
 "groups": [{"module_key","module_name","added":[…],"modified":[…],"deleted":[…],"kept":6}],
 "summary": {"add","modify","delete","keep","active_after"},
 "overrides": [], "warnings": […]}
```

- `groups[].kept` 是**数字**，其余三个是数组
- `modified[]` 项：`{feature_key, before, after, module_before, module_after}`
- **`warnings` 必须展示**，且**不只出现在 `replace` 模式** —— 实测 `union` 且无差异时也给
  「完全一致，合并不会产生任何变更」。`replace` 的「将失去 N 条现有功能」**只在这里喊**
- 错误：来源/目标不存在 → 404；来源非 `pending_review` → 409

### 3.6 `POST /api/v1/reviews/submit`（档次 `review`）

**Body**（`extra="forbid"` —— 多一个字段就 422）：
`source_id`(string), `decision`(`approved`|`rejected`|`returned`), `target_requirement_key?`,
`merge_mode?`, `reviewer_name?`, `comment?`, `edited_requirement?`, `feature_overrides?`

**响应**：`{decision, reviewer_id, status, version_no, requirement_key}`

> ⚠️ **请求体不接受 `reviewer_id`** —— 审核人身份由服务端取。前端只能传 `reviewer_name`。

#### 409 的三种签名（**已实测**，2026-09-18）

**为什么这里破例读 `detail`**：三种 409 的处理动作**不同**，而状态码三者都是 409。
这是全项目**唯一**需要读 `detail` 的地方 —— 其余一律只按状态码分支。

| `detail` 前缀 | 触发 | 前端该做什么 |
|---|---|---|
| `source_id=` … `not found` | 列表陈旧：拿着已失效的 id | 刷新待办列表 |
| `source_id=` … `is not pending review` | 重复点击 / 该条已被处理 | 刷新待办列表 |
| `该需求已被他人修改` | **乐观锁冲突** | **重新加载目标 REQ 再决定** |

**实测证据**（用不存在的 / 非待审的 source_id 打，不会改动任何数据）：

```
source_id=999999999999999999        → 409  'source_id=999999999999999999 not found'
status=committed 的来源提交裁决      → 409  'source_id=225877630925144064 is not pending review'
重复提交同一来源                     → 409  'source_id=225877630925144064 is not pending review'
```

**实现要求**：

1. **先按状态码分支，再在 409 内部弱匹配前缀**。匹配用 `startsWith` / `includes` 的固定片段，
   **不要匹配整句** —— 乐观锁那句在审核端点是「…请重新加载后**再审核**」，
   在回滚端点是「…请重新加载后**再回滚**」，**只差最后两个字**。
   前缀取 `该需求已被他人修改` 可以同时覆盖，且两者要做的事**是同一件**（重新加载）。
2. **读不到前缀时，按最安全的那个处理** —— 即「重新加载目标 REQ」。
   理由：三种里只有乐观锁会**基于陈旧的功能集产出错误的版本**，
   那个后果最重；另外两种最坏也只是多重刷一次列表。
3. 乐观锁冲突时**整个事务已回滚，没有产生任何数据** —— 提示文案不要写「提交失败」，
   要写「这条需求在你审核期间被改过，已重新加载」。

> ⚠️ **同一个字符串在不同端点含义不同**（实测）：
> `source_id=X not found` 在 `POST /reviews/submit` 是 **409**，
> 在 `GET /reviews/{id}/merge-preview` 是 **404**。
> 原因是两处的抛出点不同 —— submit 走 `workflows/commit_nodes.py:109` 的 `ValueError`，
> merge-preview 走 `application/review_service.py:141` 的 `LookupError`。
> **这是「不许按中文文本判断错误类型」这条规则最有力的一个例子**：
> 照文本判断，前端会把两种完全不同的状态混为一谈。

### 3.7 `GET /api/v1/requirements`（需求库列表）

**参数**：`q, channel, status, requester, department, business_domain, sensitivity_level,
submitted_from, submitted_to, has_version_ge, limit`（**默认 100，上限 500**，`requirements_write.py:77`）

**响应** 实测 4 条，**14 个键**（契约 §2 已更正过）：

```
requirement_key  requirement_name  final_requirement  status
business_domains(string[])  business_domain(string)  current_version(number)
feature_count(number)  source_types(string[])  requester_names(string[])
latest_source_submitted_at  first_source_submitted_at
departments(string[])  sensitivity_levels(string[])
```

> ⚠️ `business_domain` 是**兼容字段** = `business_domains[0]`，空数组时回退成 `"general"`，**不会是 null**。
> ⚠️ 实测 `departments` / `sensitivity_levels` 是 **`[]`** —— 别当「一定有值」。
> ⚠️ **本端点没有任何 id 字段**（用 `requirement_key` 定位），所以不受 §5.1 的精度问题影响。

### 3.8 `GET /api/v1/requirements/export`

**参数**：与列表**同一套**，但 `limit` **默认 500、上限 5000**（列表是 100 / 500 —— 别写成一个常量）
**响应**：CSV（`text/csv; charset=utf-8`），带 UTF-8 BOM，`Content-Disposition: attachment; filename="requirements.csv"`，
`Cache-Control: no-store`。可直接 `window.open`（前端**不要**用 `fetch` 再手动转 —— 那会丢附件语义）。

### 3.9 详情页 6 个端点

| 端点 | 参数 | 响应要点 |
|---|---|---|
| `/versions` | 无 | `{items:[{id, requirement_id, parent_version_id, parent_version_no, version_no, version_title, change_type, requirement_snapshot, change_summary, diff_payload, feature_changes, created_by, reviewed_by, created_at, status}]}` |
| `/features` | `at_version?`、`include_deleted?` | `{items:[{id, requirement_id, feature_key, content, status, ordinal, origin_source_id, origin_requirement_key, origin_version_no, removed_version_no, provenance[], module_key, module_name}]}` |
| `/diff` | `from_version?`、`to_version?` | `{requirement_key, from_version, to_version, added[], removed[], modified[], unchanged}` |
| `/trace` | 无 | `{requirement, versions:[{…, sources[]}]}` |
| `/relations` | 无 | `{items:[{id, subject_requirement_key, target_requirement_key, relation_type, reason, similarity, source_id, status, created_by, decided_by, created_at, updated_at, direction, other_requirement_key, other_requirement_name}]}` |
| `/capabilities` | 无 | `{requirement_key, requirement_name, current_version, capabilities[], constraints[]}` |

> ⚠️ `unchanged` 是**数字**，不是数组（契约 §3）。`added[]`/`removed[]` 是 feature 行。
> ⚠️ **当前版本用 `status === 'current'` 判断，不要假设 `version_no` 最大** —— 回滚后二者不一致。
> ⚠️ 回滚版本与普通版本在 `/versions` 里**长得一样**（`change_type` 恒 `modify`）；
> 要区分得读 `diff_payload.kind === 'revert'`（`/trace` 里带）。
> ⚠️ `/capabilities` 对老需求可能返回空数组 —— **空 ≠ 出错**，别让整页挂掉。
> ⚠️ `constraints[].alias_hit` 在旧快照里**可能没有这个键**（契约 §8.1，实测抓到过活实例）。

#### `GET /api/v1/requirements/{key}/titles`（实测形状，2026-09-18）

**参数**：`review_status?`
**响应**：`{requirement_key, items:[…]}`

每项（**实测**）：
```json
{"id": "226274893421871104",           // 字符串 ✅
 "requirement_id": "225548242010505216",
 "title": "巡检计划创建能力",
 "angle": "capability",                 // business_object / capability / constraint / free
 "capability_id": "225856650450305024", // angle=capability 时才有值
 "constraint_key": null,
 "source": "analysis",                  // analysis（派生）/ review（人工新增）
 "review_status": "proposed",           // proposed / confirmed / dismissed
 "decided_by": null,
 "created_by": "analysis",
 "created_at": "2026-09-17T17:35:38.558962+08:00",
 "highlight": {"feature_keys": ["F-001"], "kind": "capability", "capability_id": "225856650450305024"}}
```

**`highlight` 的键是「按 kind 变化」的** —— 实测：

| `kind` | `feature_keys` | 是否有 `capability_id` |
|---|---|---|
| `capability` | **可能有值**（实测 `["F-001"]` / `["F-014"]`），**也可能为 `[]`**（该能力在需求里没有对应功能行） | ✅ 有 |
| `business_object` | `[]` | ❌ 无该键 |
| `free` | `[]` | ❌ 无该键 |
| `constraint` | —— | **未实测**（要跑一次真实分析才能派生出来） |

> ⚠️ **`feature_keys` 为空不代表这个标题没用** —— `kind=capability` 的「提醒推送能力」
> 实测就是 `[]`（那条能力在 REQ-000015 里没有对应功能行）。前端不要据此隐藏入口。
>
> ⚠️ **当前数据里全部是 `review_status: 'proposed'`** —— 派生的和人工新增的都是。
> 契约说「列表只该展示 `confirmed` 的」，但**实测一条 confirmed 都没有**，
> 照那条实现会得到一个空列表。前端应显示全部并标注状态，而不是过滤掉。

### 3.10 `GET /api/v1/agent/runs`

**参数**：`source_id?`（**可选**）、`status?`、`run_type?`、`limit` 默认 `20`（仓储夹到 200）

> ⚠️ **`source_id` 一旦给了，`status` 和 `run_type` 会被静默忽略**（`agent_chat.py:911-916`）。
> 前端不要同时传这三个 —— 要么按来源查，要么按状态/类型筛，**不能组合**。

**响应** 实测 17 条，每项 **16 个键**：
```
id(str ✅)  run_id(str)  conversation_id  source_id  run_type  client_message_id
status  error  meta  stage  checkpoint  current_node
started_at  ended_at  created_at  updated_at
```

- `run_type`：`conversation` / `analysis`
- `status`：`queued / running / waiting_review / completed / failed / cancelled / retrying`
  —— ⚠️ **分析跑完是 `waiting_review` 而不是 `completed`**
- `current_node`：失败定位靠它
- `meta.assistant_message_id` 是**字符串**（2026-09-18 修复，见 §5.1）
- ⚠️ `started_at` / `ended_at` **可能为 `null`**（实测有 15 条 run 是）

### 3.11 `GET /api/v1/agent/runs/{run_id}` ⚠️ 见 §5.3

**参数**：路径 `run_id`（**UUID 字符串**，不是雪花 id）

**响应**：**比 §3.10 少 5 个键、多 0 个**：
```
id(str ✅)  run_id  conversation_id  client_message_id  status  error
meta  stage  checkpoint  created_at  updated_at
```
**缺**：`source_id`、`run_type`、`started_at`、`ended_at`、`current_node`

> 两个端点的 `id` **已实测一致**（同值同类型），有测试钉住 —— 见 §5.3。

### 3.12 `GET /api/v1/agent/runs/{run_id}/events`

**参数**：`after_seq` 默认 `0`（**排他**：只返回 `sequence > after_seq`）、`limit` 默认 `500`（仓储夹到 2000）
**响应**：`{items:[…], run_id, after_seq}` —— **3 个顶层键**，注意不是纯 `{items}`

每项：`{id(str ✅), run_id, sequence(int), event_type, node, payload, created_at}`

> ✅ **这个端点是归一化正确的**（`_normalize_event` 走了 `to_sid` + `as_display_iso`）。
> 契约 §6 说 `after_seq` 是排他的、客户端记住最后收到的 `id:` 原样回传即可（**不用 +1**）。

### 3.13 `GET /api/v1/agent/runs/{run_id}/invocations`

**响应**：`{run_id, tools:[…], models: []}` —— **`models` 恒为空数组**（`agent_chat.py:950`）。

`tools[]` 每项：
`id(str ✅), run_id, tool_name, arguments_summary, result_summary, status, elapsed_ms, error_message, created_at`

> ⚠️ 契约 §6 说 `models` 恒空是因为「模型调用要等 B3.1 的 ModelRegistry」。
> **B3.1 已经交付了**（`/ops/models` 有 41 条真实记录），但这个端点**仍然恒空** ——
> 模型调用明细要去 **`GET /api/v1/ops/models`** 拿（按 `run_id` 过滤）。这是本次新发现，见 §5.4。

### 3.14 `POST /api/v1/agent/runs/{run_id}/retry`（档次 **analyze**）

**请求体：无**（`retry_agent_run(run_id: str)` 没有 payload 参数）

**响应**：`{run_id(新 run), retry_of(旧 run), queued}`

前置条件（否则 404/409）：run 必须存在；`run_type` 必须是 `analysis`；`status` 必须是 `failed`/`cancelled`；
`source_id` 必须非空。**新建 run，不复活旧的**。

> ⚠️ 它是**写操作**（建 run + 走 outbox），但权限档次是 `analyze` 而非某个写档次（`auth.py:117`）。

### 3.15 `POST /api/v1/requirements/submit`（档次 `submit`）

**Body**（JSON，`extra="forbid"`）：

| 字段 | 类型 | 必填 | 约束 |
|---|---|---|---|
| `original_text` | str | **是** | `min=1, max=20000`，**纯空白会被拒** |
| `source_type` | `web`\|`email`\|`meeting`\|`manual` | 否 | 默认 `web` |
| `source_event_id` | str\|null | 否 | `max=200` |
| `requester_id` | str\|null | 否 | `max=120` |
| `requester_name` | str\|null | 否 | `max=120` |
| `metadata` | dict | 否 | 默认 `{}` |

**响应**：`{message, source_type, status, source_id}` —— ⚠️ **`source_id` 在这里是 number**（schema 声明为 `int`）。

### 3.16 `POST /api/v1/requirements/ingest`（档次 `submit`）

**multipart/form-data**。表单字段：`source_type`(默认 `web`，**自由字符串**，与 §3.15 的枚举不同)、
`requester_id`、`requester_name`、`source_event_id`、`original_text`、`metadata`（**必须能解析成 JSON 对象**，否则 400）。
**文件字段名就是 `file`**。

运行时校验（不走 Pydantic）：空文件 → 400 `uploaded file is empty`；
既无文件又无 `original_text` → 400 `either original_text or file is required`。

**响应**：同 §3.15。

> ⚠️ 前端用 `FormData` 时**不要**手动设 `Content-Type` —— 浏览器要自己加 boundary。

### 3.17 运维端点

| 端点 | 参数 | 响应 |
|---|---|---|
| `/ops/outbox` | 无 | `{counts:{pending,processing,completed,dead_letter,discarded}, dead_letters:[…], consumer}` |
| `/ops/worker` | 无 | `{consumer, counts, stale_processing, stale_timeout_seconds}` |
| `/ops/models` | `task_type?`、**`run_id?`**、`limit` 默认 `50`（1-200） | `{items:[…], routing:{unrecognized, resolved}}` |
| `/audit/events` | `limit` 默认 `50`（**1-100**，仓储不加夹） | `{items:[…]}` |

实测值：
- `/ops/outbox` → `consumer: null`，`dead_letters: []`，`counts: {"pending":0,"processing":0,"completed":9,"dead_letter":0,"discarded":0}`
- `/ops/worker` → `{consumer: null, counts: {…9…}, stale_processing: 0, stale_timeout_seconds: 300}`
- `/ops/models` → 41 条，`routing` 含 `resolved`（6 个任务）与 `unrecognized`
- `/audit/events` → 9 条，每项 **11 键**（`trace_id, event_type, aggregate_type, aggregate_id, actor_type, actor_id, before_data, after_data, result_status, error_code, created_at`）—— **注意没有 `id` 字段**

> ⚠️ **`consumer: null` 不代表「系统没有消费者」**（契约 §1.3）。它只说**这个 API 进程**没跑内嵌循环。
> 判断有没有在消费，看 `counts` 是否推进。
>
> ⚠️ **`stale_processing`** 是「消费者崩了」唯一可靠的信号 —— 只看各状态计数看不出来。
>
> ⚠️ `routing.resolved` 是**硬编码 6 个任务**（`extract/analyze/risk/narrative/embedding/vision`）。
> 未配置的任务（如 `vision`）会返回 `provider: "", model: ""` —— **那是「没配」不是「配错了」**，
> 前端要区分显示。响应里**没有 fallbacks**，只有 primary。

#### `run_id` 过滤（2026-09-18 新增，给 Run 详情页用）

`/api/v1/agent/runs/{id}/invocations` 的 `models` 字段**恒为空数组**，所以 Run 详情页要的
模型调用明细得从这里拿。新增 `run_id` 参数之前，前端只能拉全量自己筛 —— 那违反
「不在前端聚合后端数据」。

| 调用 | 顺序 | 用途 |
|---|---|---|
| `?run_id=<uuid>` | **正序** `created_at ASC` | Run 详情页：这次运行**依次**调了什么，与节点时间线对齐 |
| 不带 `run_id` | **倒序** `created_at DESC` | 运维页：**最近**调了什么（既有行为，未改） |

`run_id` 与 `task_type` **可组合**，是叠加过滤而不是二选一 —— 组合时静默丢掉一个，
调用方会拿到一份看起来合理、实则范围不对的结果（同一个陷阱见 `/agent/runs` 的 `source_id`）。

实测（造两行 → 验证 → 删除，可逆）：

```
?run_id=<uuid>                        → 2 条，时间正序 ✅
?run_id=<uuid>&task_type=risk         → 1 条，组合生效 ✅
不带 run_id（limit=200）              → 44 条全局，倒序 ✅（既有行为未变）
```

> ⚠️ **当前库里一个绑 run 的调用都没有**：42 条 `model_invocation` **全部是 `embedding` 且
> `run_id IS NULL`**，而 `agent_run` 的 17 条**全是 `conversation` 类型，`analysis` 一个都没有**。
> 原因是 `bind_run_id` 只在分析链路（`requirement_service.py:153`）调用，
> 而现存的分析都跑在 B2.1（运行追踪）交付**之前**。
> **所以这个参数对存量数据返回空是正常的**，要等新的分析跑出来才有内容。
> 前端不要把它读成「功能没生效」—— 对应地，界面上应显示「暂无模型调用记录」而非错误态。

### 3.18 `GET /api/v1/memory`

**参数**：`actor_id?`、`limit` 默认 `20`（1-50）
**响应** 实测 3 条，每项 **14 键**：
```
id(str ✅)  actor_id  kind  status  active(bool)  content
source_conversation_id  source_message_id(str ✅)  ref_requirement_key
superseded_by(str ✅)  importance  meta  created_at  updated_at
```
SQL 排除 `status='deleted'`，按 `importance` 倒序。

### 3.19 词表端点

| 端点 | 参数 | 响应 |
|---|---|---|
| `/capabilities` | `status?`（**单值**，正则 `active|deprecated|pending_confirmation`）、`q?`(max 120)、`limit` 默认 `200`（1-1000） | `{items:[{id(str), action, object, display_name, status, created_by, origin_source_id, created_at, updated_at}]}` 实测 19 条 |
| `/constraints` | 同上 | `{items:[…]}`，形状见下（**已造数据实测**） |
| `/capabilities/{id}/streams` | `constraint?`、`review_status?`、`limit?` | `{items:[{requirement_key, requirement_name, status, current_version, review_status, action, object, display_name}]}` |
| `/constraints/{constraint_id}` | 无 | **裸对象**（不是 `{items}`），字段同下 |

**`constraints` 条目的实测形状**（2026-09-18 造数据后取得，契约 §10-T4 的那条已解）：

```json
{"id": "226535744766738432",          // 字符串 ✅
 "constraint_key": "按部门筛选",
 "display_name": "按部门筛选",
 "status": "active",
 "created_by": "seed-2026-09-18",
 "aliases": ["按部门维度筛选"],          // list[str]，无别名时为 []
 "created_at": "2026-09-18T10:52:10.397120+08:00",
 "updated_at": "2026-09-18T10:52:10.397120+08:00"}
```

- `aliases` **一定存在**（无别名是 `[]`，不是缺键）—— 与 `capabilities` 相比多的就是这一个字段
- `/constraints/{id}` 是**裸对象**，`/constraints` 是 `{items:[…]}` —— 两者形状不同，别写同一个解析函数
- `status` 只可能是 `active` / `deprecated` / `pending_confirmation`，而**只有 `active` 参与匹配**

> ⚠️ **实测：19 条能力全是 `pending_confirmation`，一条 `active` 都没有。**
> 所以 `capability_match.capabilities[].matched` 在当前数据上**恒为 `false`**。
> 详见 §5.5 末尾。

> ⚠️ **`status` 在词表端点是单值**，而在 `/sources`、`/reviews/history` 是**可重复多值**。
> 写 api.js 时不能共用一个「数组参数」策略 —— 见 §5.8。

### 3.20 文档端点

| 端点 | 参数 | 响应 |
|---|---|---|
| `/documents` | `limit` 默认 `20`（**1-50**） | `{items:[{id(str), file_name, content_type, storage_uri, checksum, size_bytes, source_type, source_id(str), original_text, extracted_text, metadata, created_at}]}` 实测 1 条 |
| `/documents/{document_id}` | 路径 `document_id` 是 **int** | 裸资产对象（同上字段）。404 不存在 |
| `/documents/{document_id}/chunks` | `limit` 默认 `20`（1-50） | `{document_id, items:[{id, document_id, chunk_index, chunk_text, metadata, created_at}]}` |
| `/documents/search` | `q`(**必填**, 1-200)、`limit` 默认 `5`（1-20） | `{items:[{id, document_id, chunk_index, chunk_text, file_name, storage_uri, score, metadata, created_at}]}` 实测 5 条 |

> ⚠️ **路径参数类型不一致**：`/documents/{document_id}` 的 `document_id` 是 **int**，
> 而响应里的 `id` / `document_id` 是 **字符串**。前端从列表拿到 id 后**原样拼进 URL 即可**
> （FastAPI 会把数字字符串转回 int），**但不要 `Number()` 后再做算术**。
>
> ⚠️ `/documents/{id}/chunks` **不校验文档是否存在** —— 不存在的 id 也返回 200 + 空 items。
> 所以「空 items」在这里有两种含义，**不能据此判断文档不存在**，要用 `/documents/{id}` 的 404。
>
> ⚠️ `/documents/search` 必须声明在 `/documents/{document_id}`**之前**（路由顺序），
> 否则 `search` 会被当成 `document_id` 解析。这是后端已处理的，前端只需知道它可用。

### 3.21 `metadata.capability_match` 的形状（**已实测**，2026-09-18）

**这不是一个端点**，而是 `requirement_source.metadata` 里的一个键 —— 审核页要展示的
「这条需求有什么能力、带什么条件」全在这里。契约 §10-T4 把它的 `constraints.matched`
列为「只有读代码的证据」，**现在有实测了**。

**取得方式**：`CapabilityMatchService().match(extracted, persist=False)` ——
`persist=False` **一行都不写**（该参数就是为「预演必须真的只读」而加的）。

**实测输出**：

```json
{
  "business_object": "员工数据",
  "capabilities": [
    {"raw_text": "可以导出 Excel", "action": "导出", "object": "Excel",
     "matched": false, "proposed": true,
     "capability_id": null, "status": "pending_confirmation"}
  ],
  "constraints": {
    "matched": [
      {"raw": "按部门维度筛选",              // 命中别名
       "constraint_id": "226535744766738432",  // 字符串 ✅
       "constraint_key": "按部门筛选",         // 归到哪个正式条件
       "alias_hit": true}                    // 命中的是别名，不是正式键
    ],
    "unmatched": [{"raw": "按区域层级导出"}]   // 只有 raw 一个键
  },
  "summary": {
    "capability_total": 2, "capability_matched": 0, "capability_proposed": 2,
    "constraint_matched": 1, "constraint_unmatched": 1
  }
}
```

**`alias_hit` 的两个分支都实测到了**：

| 输入 | `alias_hit` |
|---|---|
| `"按部门维度筛选"`（登记过的**别名**） | `true` |
| `"按部门筛选"`（**正式键**本身） | `false` |

**几条必须知道的**：

1. **`capability_id` 在 `proposed: true` 时可能是 `null`** —— 上面那份实测就是
   （`persist=False` 不建提案行，自然没有 id）。真实分析走 `persist=True`，
   提案落库后才会带上雪花 id。**契约 §4.1 的样例里 proposed 却带着 id，那容易误导。**
2. **`constraints.unmatched[]` 里只有 `raw` 一个键** —— 契约的样例是一致的，但它**没有
   `alias_hit`**，因为压根没命中。别写 `c.alias_hit ? … : ''` 去读 unmatched 的条目。
3. **`matched` 的能力未必存在** —— 只有 `status='active'` 的能力词条才参与匹配。
   **实测：库里 19 条能力全是 `pending_confirmation`，一条 `active` 都没有**，
   所以 `matched` 在当前数据上**恒为 `false`**（见 §5.5 末尾）。

---

## 4. 实测覆盖情况（哪些打了真实库）

**已实测（A 级证据，23 个 GET 端点，2026-09-18）**：
`stats/overview`、`sources`、`reviews/pending`、`reviews/history`、`requirements`、
`requirements/search`、`requirements/features/search`、`documents`、`documents/search`、
`capabilities`、`constraints`(空)、`audit/events`、`health/db`、`health/llm`、`health/embedding`、
`ops/outbox`、`ops/models`、`ops/worker`、`agent/runs`、`memory`、`memory/context`、`conversations`、
`requirements/{key}/versions`(`verify_api_contract.py` 覆盖 REQ-000015)

**仅源码取证（B 级，写端点未在真实库执行）**：全部 `POST` / `PATCH` / `DELETE`，
以及依赖特定数据的 GET（`reviews/{id}/detail`、`merge-preview`、`capabilities/{id}/streams`、
`requirements/{key}/titles`、`sources/{id}/trace`、`operations/dead-letters/*`）。

> **写端点为什么不在真实库上打**：它们会**真的建 run、真的提交审核、真的回滚版本**。
> 按本题要求 §六.7「未通过接口验证时不得进入下一阶段」，写端点的验证要**在专属测试库或
> 事务回滚的测试里**做 —— 那是阶段 6 每个页面实现时的事，不是现在。
> `tests/integration/` 里已有覆盖（`test_review_commit_path.py`、`test_requirement_revert.py` 等）。

---

## 5. 实测发现的问题（**这是本次盘点的最大产出**）

### 5.1 ~~🔴 P0 · 两个端点返回 number 型雪花 ID~~ ✅ **已修（2026-09-18 同日）**

**盘点时发现**下列端点把雪花 ID 当 JSON number 发出去，违反契约 §1.1：

| 端点 | 违规字段 | 盘点时实测值 | 出处 |
|---|---|---|---|
| `GET /api/v1/agent/runs` | `id` | `225549012554481664`（int） | `repositories/agent_run.py` 返回 `dict(row)` 裸行 |
| `GET /api/v1/agent/runs/{run_id}` | `id` | —— | `repositories/chat.py` 显式 `int(row["id"])` |
| `GET /api/v1/agent/runs/{id}/invocations` | `tools[].id` | —— | 同上，返回裸行 |
| `GET /api/v1/memory` | `id`、`source_message_id`、`superseded_by` | `225102660003430400`、`225102609537564672` | `_normalize_memory_row` 未调 `to_sid` |

**当时的危险程度：潜伏，不是已发作。** 这些值**恰好**能被 double 精确表示（末尾零足够多，`seq=0`）。
但契约 §1.1 明说那不是可依赖的性质 —— 库里**已经有 2 个不可精确表示的 ID**
（`requirement_title_candidate.id`、`outbox_event.id`，由 `verify_api_contract.py` 扫出）。
一旦出现 `seq≠0` 的行，前端就会**悄悄拿到一个差 1 的 id**。

> ⚠️ **仓库自带的检查脚本当时为什么没报？** 因为它的 `SPEC` **只覆盖 10 个端点**，
> 而 `agent/run*`、`memory*`、`conversations*` **一个字都没提**。
> 它的 `✅ 无精度风险` 是**在它看到的那 10 个端点上成立的结论**，不是全量结论。

**已做的修复**：

1. `agent_run.py` 新增 `_normalize_run_row` / `_normalize_tool_row`，套用到
   `create_run` / `get_run` / `list_by_source` / `list_recent` / `list_tool_invocations`
2. `chat.py::_normalize_run_row` 的 `int(row["id"])` → `to_sid(row["id"])`
3. `memory.py::_normalize_memory_row` 的三个 id 字段改 `to_sid`
4. `verify_api_contract.py` 的 SPEC 补入 `/agent/runs`、`/memory`、`/sources`
5. 回归测试：`test_id_serialization.py` 的 `ID_ENDPOINTS` 加两条 + 三条新用例

> **补 SPEC 立刻又抓出一个**：把 `/agent/runs` 加进去后，脚本的大整数扫描报出
> **`meta.assistant_message_id` 也是 number**（嵌在存储型 JSON 里，与
> `provenance[].source_id` 同一类）。它此前一直在盲区里，实测 **15 条 run** 带这个字段。
> 修法沿用契约 §10-T1 第 2 条「**读时转**」：落在 `domain/agent_run.py::stringify_run_meta`，
> 由两个仓储的 run 归一化共用。
>
> 这是本次最值得记的一条：**覆盖边界就是结论边界** ——
> 脚本报「无风险」不是因为它检查过，是因为它没看。

**对前端的影响**：修好之后，`api.js` **不需要为这几个字段写任何兼容分支**，一律原样透传。
（`run_id` 本来就是 UUID 字符串。）

### 5.2 ~~🔴 P0 · `/agent/runs` 的时间格式与全站不一致~~ ✅ **已修（2026-09-18 同日）**

| 端点 | 盘点时 `created_at` 实测 | 修复后 |
|---|---|---|
| `/agent/runs` | `'2026-09-15T09:31:15.092449Z'`（**UTC，无偏移**） | `'2026-09-15T17:31:15.092449+08:00'` |
| `/sources` | `'2026-09-16T15:17:03.829708+08:00'` | 不变 |
| `/reviews/pending` | `'2026-09-16T12:07:12.925786+08:00'` | 不变 |
| `/documents` | `'2026-09-14T11:55:10.118948+08:00'` | 不变 |

原因：`/agent/runs` 走 FastAPI 的 datetime 编码器，其余走 `as_display_iso`。

**影响其实有限** —— `new Date()` 对两种格式都能解析正确，所以**不是数据错误**；
但如果前端用字符串切片格式化（`iso.slice(11,16)`）会差 8 小时。
`core.js` 现有的 `fmt.time()` 用的是 `new Date()`，**是安全的**；
拆分成 `format.js` 时**必须保持这一点**（已加测试钉住）。

### 5.3 🟡 P1 · `/agent/runs/{run_id}` 与 `/agent/runs` 字段集不同

同一个 run，从列表拿和从详情拿，**字段不一样**：
列表多 `source_id / run_type / started_at / ended_at / current_node`，详情**全都没有**。

**影响**：Run 详情页若先展示列表项、再拉详情补充，会**丢掉 `source_id` 和 `run_type`**。
**建议**：详情页**同时用列表页传来的对象做底**，只用详情端点补 `checkpoint` 等字段。
这条要在阶段 8 实现时确认是否够用（见 §6 待办）。

### 5.4 ~~🟡 P1 · `/agent/runs/{id}/invocations` 的 `models` 恒空~~ ✅ **已解决**

契约 §6 的解释是「模型调用要等 B3.1 的 ModelRegistry」——**B3.1 已经交付了**：
`GET /api/v1/ops/models` 实测有真实模型调用记录，字段含 `run_id`。

**处置**（2026-09-18）：给 `/ops/models` 加了 `run_id` 参数，见 §3.17。
前端不再需要拉全量自己筛。`/agent/runs/{id}/invocations` 的 `models` **仍然恒空，
不要用** —— 那条路径后端没有改，也不打算改。

> ⚠️ **附带发现**：`run_id` 过滤对**存量数据返回空**是正确的 —— 库里 42 条调用全是
> `embedding` 且 `run_id IS NULL`，17 个 run 全是 `conversation` 类型。
> 详见 §3.17 末尾的说明。

### 5.5 ~~🟡 P1 · 三个「空的形状」仍未验证（契约 §10-T4 遗留）~~ ✅ **已造数据并实测（2026-09-18）**

| 形状 | 结果 | 说明 |
|---|---|---|
| `/constraints` 的 `items[]` | ✅ **已实测** | 造了 2 条 + 1 个别名，形状见 §3.19 |
| `capability_match.constraints.matched[]` | ✅ **已实测** | `alias_hit` 的 `true` / `false` 两个分支都跑到了 |
| `/requirements/{key}/titles` 的 `highlight` | ⚠️ **一半** | `business_object` / `capability` / `free` 三种跑到了；**`constraint` 仍未验证** |

#### 造了什么（走正式代码路径，不是裸 SQL）

| 数据 | 路径 |
|---|---|
| 条件词表 2 条（`按部门筛选`、`按门店维度聚合`，`status=active`） | `ConstraintVocabRepository.create()` |
| 1 个别名（`按部门维度筛选`） | `POST /api/v1/constraints/aliases`（产品路径） |
| 候选标题 2 条（`angle=free`） | `POST /api/v1/requirements/REQ-000015/titles`（产品路径） |

`capability_match` 的形状用 **`CapabilityMatchService().match(..., persist=False)`** 拿到 ——
**一行都没写**（该参数就是为「预演必须真的只读」加的）。见 §3.21。

#### ⚠️ 契约 §10-T4 有一条**已过期**

它说「`requirement_title_candidate` 表 **0 行**（2026-09-16）」——
**实测该表有数据**，4 行是 2026-09-17 17:35 分析链路派生的（`source='analysis'`）。
所以 `/titles` 的形状**本来就是有真实数据的**，只是那次复跑的时间点早于它。

> **顺带亲眼看到了 §1.1 警告的那个精度事故**：实测的标题 `id` 里有
> `226274893426065408` 与 `226274893426065409` —— **相邻两行**，
> 而后者正是 `verify_api_contract.py` 扫出的「不可被 double 精确表示」的那个值
> （`JSON.parse` 后变成前者）。**两行同时存在**，所以一旦以 number 发出，
> 前端点「提醒推送能力」会打开「任务指派能力」。
> 实测响应里 `id` 是**字符串** ✅ —— 这个坑目前是堵住的。

#### ⚠️ 仍未验证的两处（都卡在「要跑一次真实分析」）

1. `highlight.kind === 'constraint'` —— 派生逻辑在分析链路里
2. `capability_match.constraints.matched[]` **出现在真实审核页上** ——
   目前只有 `persist=False` 的直接调用证据，没有一条真实来源带它

> 另有一条**运营层的发现**（不是接口问题）：库里 **19 条能力全部是 `pending_confirmation`，
> 一条 `active` 都没有**。而只有 `active` 参与匹配（`find_exact(only_active=True)`）。
> **所以 `capability_match.capabilities[].matched` 在当前数据上恒为 `false`**，
> 审核页的「命中能力」区块会永远显示为「AI 提议」。
> 这是设计如此（未确认的能力不参与后续匹配），但**没人确认过任何一条** ——
> 要么是审核流程没走到那一步，要么是裁决入口没人用。前端按「全 proposed」的状态设计即可，
> 不要假设一定有 confirmed。

### 5.6 🔴 P0 · 把 SPEC 补全后，**又炸出 13 处 number 型雪花 ID** ✅ 已修

`verify_api_contract.py` 的 SPEC 从 10 条补到 43 条（覆盖全部只读端点）之后，
脚本当场报出 **13 个此前完全不可见的 number 型 id 字段**：

| 端点 | 字段 | 出处 |
|---|---|---|
| `/audit/events` | `items.before_data.source_id` | `audit.py` 只做 `dict(row[...])` |
| `/conversations/{id}/messages` | `items.id` | `chat.py::_normalize_message_row` 的 `int(row["id"])` |
| `/agent/chat/{session_id}` | `history[].id` | 同上（同一函数） |
| `/requirements/{key}/trace` | `versions[].version_id` | `trace_by_requirement_key` 用 int 做分组键，顺手发出去了 |
| `/reviews/{id}/detail` | `metadata.requirement_id`、`metadata.version_id`、`metadata.trace.source_id`、`metadata.trace.version_id` | `_stringify_source_metadata` 没覆盖这几个键 |
| `/sources/{id}/trace` | `source.metadata.*`（同上四个） | 同一个 helper |
| `/reviews/{id}/merge-preview` | **`source_id`** | `review_service` 返回时没转 |

**已全部修掉**，脚本回到 `✅`，类型表 64 处 id 字段**全是 string**。

> ⚠️ 其中 `merge-preview` 的那条最值得单说：**同一个 `source_id`，
> `/reviews/pending` 发字符串、`/reviews/{id}/merge-preview` 发 number**。
> 前端把待办项和预演结果拼在一起时就会撞上 —— 正是契约 §8.1 警告的
> 「同一字段两种类型」，只不过这次是在不同端点之间。
>
> `merge-preview` 的 `source_id: to_sid(...)` 是**一行**的改动，
> 但它能存在的唯一原因是**没有人核对过那个端点的字段**。

**审计载荷那一处是唯一有设计取舍的**：`before_data` / `after_data` 是任意聚合的快照，
没法预先枚举键，所以用了**按键名递归**（键是 `id` 或以 `_id` 结尾）。
它与契约禁止的「全局 JSON 编码器」不是一回事 —— 契约反对的是**按值大小**判断
（那会让同一字段在小 id 时是 number、大 id 时是 string），而按**键名**判断是确定性的，
同一个键在任何数据上类型都一致。UUID 字符串经 `to_sid` 原样返回，不会被改坏。

### 5.7 ✅ 新增：覆盖率闸门（这条比上面修复的都重要）

只补今天这 43 条，明天加个端点照样会溜过去。所以给脚本加了一道闸门：
**每一条活路由都必须在 `SPEC`（要核对）或 `EXCLUDED_ROUTES`（说清为什么不核对）
里表过态，否则脚本直接红。**

`EXCLUDED_ROUTES` 收的是 30 条写端点与非 JSON 端点（SSE / CSV / HTML / multipart），
每条都写了理由。它们不是「不重要」，是**这个只发 GET 的脚本核对不了** ——
写路由的权限分类另有 `tests/integration/test_api_auth.py` 兜底。

效果立竿见影：加完闸门当场就报了 2 条漏网的（`/reviews/{id}/detail`、
`/reviews/{id}/merge-preview`），补进去之后又炸出上面那 13 个字段。

> ⚠️ 闸门第一版自己写错了 —— `live` 是 `(method, path)` 元组集合，
> 而 `spec_paths` 只有路径字符串，集合相减一个都减不掉，于是**每条路由都被报成未覆盖**。
> 留着这段是因为它说明了闸门的意义：**它自己响了，所以被修了**。
> 如果当时静默地「看起来过了」，这个 bug 会一直躺在那里。

### 5.8 🟢 P2 · 同名参数在不同端点是不同类型

| 参数 | 多值？ | 出现在 |
|---|---|---|
| `status` | **可重复、多值** | `/sources`、`/reviews/history` |
| `status` | **单值**（正则约束） | `/capabilities`、`/constraints` |

`api.js` 若写一个统一的「数组转重复 query」助手，**在这两处会静默行为不同**。
建议在 api.js 里**逐端点显式声明参数形状**，而不是靠通用约定。

### 5.9 ✅ 已确认**没有**问题的一件事

`/api/v1/agent/runs` 的 `source_id` **确实已改成可选**（工作计划缺口 #6），
实测不带 `source_id` 也能拿到最近 17 条 run。`source_id` 分支与 `status/run_type` 分支
**互斥**（给了 source_id 就忽略另外两个），这条契约没写，已在 §3.10 注明。

---

## 6. 进入下一阶段前必须拍板/确认的事

| # | 事项 | 影响阶段 | 状态 |
|---|---|---|---|
| 1 | **`/agent/runs` 的 `id` 是 number** | 阶段 8 | ✅ **已修**，见 §5.1。前端**不写兼容分支** |
| 2 | **`memory` 页面的删除**要用 number 型 `id` 拼 URL | 阶段 12 | ✅ **已修**（`id` / `source_message_id` / `superseded_by` 全部字符串化） |
| 3 | **Run 详情页的模型调用**走哪条路 | 阶段 8 | ✅ **已定并实现** —— `/ops/models` 加 `run_id` 参数，见 §3.17 |
| 4 | **409 的三种签名**要不要读 `detail` | 阶段 6 | ✅ **已定** —— 先按状态码分支，409 内弱匹配前缀，读不到按最安全的处理。见 §3.6 |
| 5 | **三个空形状**要不要先造数据 | 阶段 11 | ✅ **已定：先造** |
| 6 | ~~**方案文档的「已定 React + TS + Vite」**~~ | 全局 | ✅ **已改** —— `docs/方案_前端工作台.md` §6.1/§6.2/§9 已更正为原生 ES Modules + 新目录结构 |
| 7 | **`verify_api_contract.py` 的 SPEC 只覆盖 13 个端点**（原 10 个） | 全局 | ⏳ **待定** —— 本次补了 3 个（都是出问题的那几个）。**要不要补全到 73 条？** 不补的话下次还会有「全绿但有问题」 |

> 第 7 条是本次最有价值的一条。已经证明过一次：**把 `/agent/runs` 加进 SPEC，
> 立刻又扫出 `meta.assistant_message_id` 是 number** —— 它此前躺在盲区里，
> 而 15 条 run 都带着这个字段。

---

## 7. ⛔ 明确**不接入**的端点（后端没有，前端不许编）

| 缺失能力 | 对应页面 | 处置 |
|---|---|---|
| 影响分析（依赖传播 / 环检测） | 需求详情 · 影响范围 | 显示「**后端未提供**」，**不要拿关系列表充数** —— 两者不是一回事 |
| 导入任务进度（按来源聚合的任务状态） | 输入中心 | 只能展示单条来源的 `processing_status` |
| 截图 / 视觉输入 | 输入中心 | 视觉模型未接 |
| 渠道状态 | 系统运维 | 只展示飞书 webhook 配置说明 |
| 文档版本链 | 来源与知识 | 数据层有、**HTTP 层一个字都没有**（契约 §10-T6） |

---

## 8. 与 `docs/api-contract.md` 的差异清单

本次盘点**新发现**、契约尚未记录的：

| # | 差异 | 位置 | 状态 |
|---|---|---|---|
| 1 | `/agent/runs`、`/memory`、`invocations.tools[]` 返回 **number 型雪花 ID** | 契约 §1.1 声称「一律字符串」 | ✅ **代码已修**；契约 §1.1 原文无需改（它本来就是对的，是实现没跟上） |
| 2 | `/agent/runs` 的时间**不走 `as_display_iso`** | 契约 §1 声称统一 ISO | ✅ **代码已修** |
| 3 | `/agent/runs/{run_id}` 与 `/agent/runs` **字段集不同** | 契约 §6 未提 | ⏳ 待补进契约 |
| 4 | `/agent/runs` 的 `source_id` 会**静默忽略** `status`/`run_type` | 契约 §1.3 未提 | ⏳ 待补进契约 |
| 5 | `/invocations` 的 `models` 恒空，但 `/ops/models` 已有真实数据 | 契约 §6 的解释已过期 | ⏳ 待补进契约 |
| 6 | `/ops/models` 的 `routing.resolved` 是**硬编码 6 个任务**、未配置时返回空串 | 契约 §1.3 未提 | ⏳ 待补进契约 |
| 7 | **契约 §7 说死信 retry/discard「没有鉴权」已过期** —— 实测要求 `ops` 档次 | 契约 §7 | ⏳ **契约要改**（代码是对的） |
| 8 | `/agent/run`（单数，同步、会写库）与 `/requirements/submit`（走 outbox）语义不同 | 契约未区分 | ⏳ 待补进契约 |
| 9 | `meta.assistant_message_id` 也是 number（**补 SPEC 后才扫出来**） | 契约 §1.1 覆盖范围内 | ✅ **代码已修** |

> 差异 #7 值得单独说：契约 §7 写「这两个**没有鉴权**（见 current-state §三 遗留 8）」，
> 但 B1（2026-09-17）之后 `/api/v1/ops/` 全部要求 `ops` 档次。**照契约写前端会以为可以裸调**，
> 实际会拿到 403。这是「文档比代码旧」的又一例 —— **这次要改的是文档，不是代码**。

---

## 附：本次盘点的原始证据

| 结论 | 证据 |
|---|---|
| 活路由 73 条 / 67 个路径 | `app.openapi()` 遍历（含 `GET /`、`GET /health`） |
| 67 个端点响应 schema 是「自由 object」 | 同一份导出：`grep -c "resp 200: object(自由)"` |
| 契约只详细记录 50 个路径 | 正则抽取 `api-contract.md` 的 `METHOD /path` 后归一化对比 |
| 23 个 GET 端点实测通过 | `TestClient` 打真实库（`settings.frontend_token()` 非空，鉴权头有效） |
| `agent/runs` 的 `id` 是 number | 实测响应 `{"id": 225549012554481664, …}` |
| 库里 2 个 ID 不可被 double 精确表示 | `python scripts/verify_api_contract.py --only ids` |
| 该脚本 SPEC 不含 agent/memory/conversations | `grep -c` 命中 `0` |
| 73 条路由的机器可读清单 | `docs/baseline/routes_live.json`（本次生成，**替代已过期的 `routes.json`**） |

> ⚠️ **`docs/baseline/routes.json` 与 `openapi.json` 已过期**（生成于 2026-09-17 10:57，
> 早于同日 17:57 的 `f364412`「补齐 6 个工作台端点」）—— 它们里**没有** `stats/overview`、
> `sources`、`reviews/history`、`ops/models`、`ops/worker`。
> 本次生成的 `routes_live.json` 是当前事实，**建议以它为准**；旧文件未被删除，保留追溯。
