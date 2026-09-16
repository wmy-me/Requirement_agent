# 前端接口契约（需求治理域）

> **给重写前端的人看的。** 只写「后端实际返回什么」，不写渲染。
> 字段名以**实测**为准（2026-09-15 首次逐字段核对，**2026-09-16 复跑一次**，方法见 §9）。
>
> 为什么要这份文档：字段名对不上是重写前端最常见的坏法，而它**跑起来才炸**。
> 实测中抓到过 `constraints[].alias_hit` 两个分支形状不一致（§8.1）。
>
> **2026-09-16 复跑抓到三处「文档写错了」**（不是代码坏了，是契约本身不准）：
> ① §1 说「雪花 ID 一律字符串」——**只有审核端点是这样**，其余全是 JSON number（§1.1）
> ② 需求库列表实际 **14 个字段**，不是 10 个（§2）
> ③ 对话 SSE 的事件名写错：**没有 `delta`/`message`**，实际是 `artifacts`/`narrative`（§6）
>
> ⚠️ 这三条都会让重写的前端「照文档实现却跑不通」。§10 是**还没做完的清单**。

---

## 1. 全局约定

| 约定 | 说明 |
|---|---|
| **列表一律 `{"items": [...]}`** | 不是裸数组 |
| 时间 | ISO 8601 带时区（`as_display_iso`），可直接 `new Date()` |
| 错误 | 404 = 不存在；409 = 状态冲突（detail 是中文说明）；422 = 参数不合法 |
| 日期筛选的 `to` | 前端要补 `T23:59:59`，否则当天数据会被排除 |

### 1.1 ⚠️ ID 类型**不是**全局统一的字符串（2026-09-16 更正）

旧版这里写「雪花 ID 一律用字符串」。**这是错的** —— 字符串化只做在**审核端点**一处，
其余端点把雪花 ID 当 **JSON number** 发出来。

| 端点 / 字段 | JSON 类型 | 能安全 `Number()`？ |
|---|---|---|
| `/reviews/pending`、`/reviews/{id}/detail` 的 `source_id` | **string** | 本来就是字符串 |
| `/requirements` 列表 | 无雪花 ID | — |
| `/requirements/{key}/versions` 的 `id`/`requirement_id`/`diff_payload.source_id` | **number** | ⚠️ 见下 |
| `/requirements/{key}/features` 的 `id`/`origin_source_id`/`provenance[].source_id` | **number** | ⚠️ |
| `/requirements/{key}/diff` 的 `before_data.source_id` | **number** | ⚠️ |
| `/requirements/{key}/relations` 的 `id`/`source_id` | **number** | ⚠️ |
| `/requirements/{key}/capabilities` 的 `feature_id`/`capability_id` | **number** | ⚠️ |
| `/capabilities` 的 `id`、`metadata.capability_match.capabilities[].capability_id` | **number** | ⚠️ |
| `/requirements/submit` 的 `source_id` | **number** | ⚠️ |

**为什么现在没炸**：库里的雪花 ID 目前 `worker=0 且 seq=0`，末尾 22 位以上全是 0，
58 位的值只需要 36 位有效位，双精度（53 位尾数）装得下。**这不是设计保证，是运气。**

**什么时候会炸**：同一毫秒内产生第二个 ID（`seq≠0`）或换了 worker 号，低位就不再是 0。
**这件事已经真实发生过一次**，记录在 `infrastructure/db/repositories/requirement.py:331`：

> 实测 `224103804432285696 → ...700`、`225111676653928448 → ...450`，
> 回传时就成了「source_id not found」→ 审核 409。

**所以**：`/reviews/pending` 的 `source_id` 是**故意**字符串化的（那是审核回传的路径参数）。
其余端点**只是今天恰好安全**。`relations` 的 `id` 同样被拼进 URL 做路径参数
（`app.js:1428`），与当年那次的失败模式**完全同源**。详见 §10-T1。

**状态枚举**（各上下文含义不同，别混用）：

| 字段 | 取值 |
|---|---|
| `requirement_master.status` | `active` / `archived` / `deleted` |
| `requirement_version.status` | `draft` / `pending_review` / **`current`** / `superseded` |
| `requirement_version.change_type` | `new` / `add` / `modify` / `delete`（**这是「本版做了什么变更」，不是状态**） |
| `requirement_source.processing_status` | `received` / `extracting` / `analyzing` / `pending_review` / `approved` / `rejected` / `committed` / `failed` |
| `capability.status` | `active` / `deprecated` / `pending_confirmation` |
| `feature_capability.review_status` | `proposed` / `confirmed` / `dismissed` |
| `requirement_relation.status` | `proposed` / `confirmed` / `dismissed` |
| `requirement_title_candidate.review_status` | `proposed` / `confirmed` / `dismissed` |

> ⚠️ **只有 `confirmed` / `active` 是「人工确认过」的。** `proposed` /
> `pending_confirmation` 是 AI 提议，展示时必须标出来 —— 否则人会把模型猜的
> 当成已确认的（项目此前反复踩过「假数据冒充模型输出」）。

---

## 2. 需求库（列表页）

### `GET /api/v1/requirements`

入参：`q, channel, status, requester, department, business_domain, sensitivity_level, submitted_from, submitted_to, has_version_ge, limit(1-500, 默认100)`

出参 `{"items": [...]}`，每项 **14 个字段**（2026-09-16 实测；旧版写 10 个，漏了 4 个）：

| 字段 | 类型 | 备注 |
|---|---|---|
| `requirement_key` | string | `REQ-000015` |
| `requirement_name` | string | |
| `final_requirement` | string | 当前 active 功能的拼接（换行分隔） |
| `status` | string | |
| `business_domains` | string[] | **数组**；另有兼容字段 `business_domain` 取首个 |
| `current_version` | number | |
| `feature_count` | number | |
| `source_types` | string[] | |
| `requester_names` | string[] | |
| `latest_source_submitted_at` | string\|null | |
| `departments` | string[] | **旧版漏写**：全部来源的部门去重 |
| `sensitivity_levels` | string[] | **旧版漏写**：全部来源的密级去重 |
| `first_source_submitted_at` | string\|null | **旧版漏写**：最早一条来源的时间 |
| `business_domain` | string | 兼容字段 = `business_domains[0]`，**空数组时回退成 `"general"`**（不会是 null） |

> 实测有需求的 `departments` / `sensitivity_levels` 是 **`[]`**（历史数据没填部门/密级）。
> 前端别把它们当成「一定有值」——筛选下拉目前也因此是空的（见 §10-T5）。

### `GET /api/v1/requirements/export`
同一套入参，返回 CSV（UTF-8 BOM），可直接 `window.open`。
⚠️ **`limit` 的默认值与上限不同**：列表是 `1-500 / 默认 100`，导出是 `1-5000 / 默认 500`。

---

## 3. 详情页

前端并发拉 6 个端点（`app.js:1333-1340`）：versions / trace / features / diff / relations /
capabilities（capabilities 那个带 `.catch` 兜底，见 §8.3）。

### `GET /api/v1/requirements/{key}/versions`
`{"items":[{id, requirement_id, parent_version_id, parent_version_no, version_no, version_title, change_type, requirement_snapshot, change_summary, diff_payload, feature_changes, created_by, reviewed_by, created_at}]}`

> `id` / `requirement_id` 是 **number**（雪花）。`diff_payload` 里也带 `source_id`（number）。

### `GET /api/v1/requirements/{key}/features`
入参 `at_version`（可选）、`include_deleted`。
`{"items":[{id, requirement_id, feature_key, content, status, ordinal, origin_source_id, origin_requirement_key, origin_version_no, removed_version_no, provenance, module_key, module_name}]}`

> `module_name` 就是详情页的 📦 标签；为 `null` 表示该功能不归属任何模块。
> `id` / `requirement_id` / `origin_source_id` / `provenance[].source_id` 都是 **number**。

### `GET /api/v1/requirements/{key}/diff`
入参 `from_version` / `to_version`（都可空，默认相邻两版）。
出参 `{requirement_key, from_version, to_version, added[], removed[], modified[], unchanged}`

- `added[]` / `removed[]`：feature 行（**与 `/features` 同一套字段**，含 `module_key`/`module_name`）
- `modified[]`：`{feature_key, before, after}`
- `unchanged`：**数字**（不是数组）

> 实测自洽：REQ-000015 的 `v1→v3` 得 `added=4`（v2 引入 1 + v3 引入 3）、`unchanged=12`，
> 而 `features?at_version=1` 得 12 条（正是 v1 当时的功能数）。

### `GET /api/v1/requirements/{key}/trace`
需求主体 + 逐版本快照 + 每版来源链。顶层 `{requirement, versions}`。
`requirement` 里有 `lock_version`（**当前是死列**，见 §10-T7）。

### `GET /api/v1/requirements/{key}/relations`
`{"items":[...]}`，**双向**（我指向别人 + 别人指向我），每项：
`{id, subject_requirement_key, target_requirement_key, relation_type, reason, similarity, source_id, status, created_by, decided_by, created_at, updated_at, direction, other_requirement_key, other_requirement_name}`

- `direction`：`outgoing`（本需求 → 对方）/ `incoming`
- `relation_type`：`duplicates_of` / `related` / `conflict` / `depends`
- ⚠️ **`id` 与 `source_id` 都是 number**（与审核端点的 `source_id` 是 string 不一致）。
  `id` 会被拼进 PATCH 的 URL —— 与 §1.1 记录的精度事故同源。

### `PATCH /api/v1/requirements/relations/{relation_id}`
Body `{"status": "confirmed" | "dismissed"}`。**不接受改回 `proposed`**（撤回需重新分析）。404 不存在。

---

## 4. 审核页

### `GET /api/v1/reviews/pending?limit=N`
`{"items":[...]}`，`limit` 范围 `1-100`，默认 20。

每项的**完整字段**（2026-09-16 实测）：

```
source_id(str)  source_type  source_event_id  requester_id  requester_name
original_text   extracted_text  original_payload  metadata
processing_status  submitted_at  updated_at
```

> `source_id` 是**字符串**（故意，见 §1.1）。`submitted_at`/`updated_at` 可直接 `new Date()`。

**前端要读的都在 `metadata` 里**。实测**顶层 7 个键**（旧版只写了 4 个），
下表最后一行是嵌在 `analysis` 里的子字段：

| 路径 | 内容 |
|---|---|
| `metadata.extracted` | `{requirement_title, summary, business_object, requirements[], modules[], raw_text, requester_name, ...}` |
| `metadata.analysis` | `{duplicate, related, conflict, independent, reasoning, candidates[], suggestion}` |
| `metadata.analysis.suggestion` | **2026-09-16 新增**，见 §4.2 |
| `metadata.risk` | `{quality_risk, change_risk, technical_impact_risk, confidence, source}` |
| `metadata.capability_match` | **批次 2 起有**，见 §4.1 |
| `metadata.business_domain` | string，抽取出的业务域 |
| `metadata.retrieval_filters` | 检索时用的筛选条件快照 |
| `metadata.standardized_document` | 渠道归一化后的文档（有渠道来源时才有） |

> ⚠️ **`metadata` 的键是逐个来源长出来的，不是固定集合。** 实测 4 条待办里，
> `capability_match` 只有 2 条有、`priority` 只有 1 条有。**每个键都要容错**
> （`(meta.capability_match || {})`），不要写 `meta.capability_match.capabilities`
> 这种一步到底的取值 —— 老数据上必炸。

#### 4.1 `metadata.capability_match` 形状

```json
{
  "business_object": "员工数据",
  "capabilities": [
    {"raw_text": "…", "action": "导出", "object": "Excel",
     "matched": true,  "capability_id": 225856650450305024, "display_name": "导出 Excel"},
    {"raw_text": "…", "action": "查询", "object": "在职状态",
     "matched": false, "proposed": true, "capability_id": 225829988228661248,
     "status": "pending_confirmation"}
  ],
  "constraints": {
    "matched":   [{"raw": "按部门维度筛选", "constraint_id": …, "constraint_key": "按部门筛选", "alias_hit": true}],
    "unmatched": [{"raw": "按区域层级导出"}]
  },
  "summary": {"capability_total": 2, "capability_matched": 1, "capability_proposed": 1,
              "constraint_matched": 1, "constraint_unmatched": 1}
}
```

- `matched: true` → 命中已确认的词表
- `matched: false` → 新提案（`status: pending_confirmation`，**不参与后续匹配**）
- `constraints.unmatched` 里是**没入词表的条件原文**，要单独展示让人工决定
- ⚠️ **`capability_id` 是雪花 number，不是小整数** —— 旧版文档的示例写成 `1` / `9` 是误导。
- ⚠️ `constraints.matched` **至今没有真实数据**（`constraint_vocab` 表是空的），
  这一支的形状**只有读代码的证据**（`application/capability_match_service.py:220`）。见 §10-T4。

#### 4.2 `metadata.analysis.suggestion`（2026-09-16 新增）

「该新建需求主线，还是追加到既有的」——**只是建议，人工确认才算数**。

```json
{"action": "append_to", "target_requirement_key": "REQ-000015",
 "confidence": 0.9, "reason": "当前需求与 REQ-000015 属于同一门店巡检管理系统…"}
```

| 键 | 取值 |
|---|---|
| `action` | `create_new` / `append_to` |
| `target_requirement_key` | string 或 `null`（`action=create_new` 时为 `null`） |
| `confidence` | `0.0 ~ 1.0` |
| `reason` | string（最长 500 字） |

- **模型判不准时整个键不存在**（不是 `null`）—— 实测 4 条待办里只有最新那条有该键，
  且它在另外 3 条里**完全不存在**。取值时必须 `(analysis.suggestion || {})`。
- 启发式回退路径（LLM 不可用）**永远不给建议**，键同样不存在。
- 后端保证：`append_to` 一定带 `target_requirement_key`。给不出有效建议时宁可整条不给，
  **不会编一个默认值** —— 所以「没有这个键」= 「模型没判」，不是「判成了新建」。
- ⚠️ **前端目前完全没渲染这个字段**（`app.js` 里没有 `suggestion` 字样）。见 §10-T5。

### `GET /api/v1/reviews/{source_id}/detail`
与 `/reviews/pending` 的单项**同一形状**（字段完全一致）。不存在 → 404。
⚠️ **前端没有在调这个端点**，契约此前也没写。见 §10-T5。

### `GET /api/v1/reviews/{source_id}/merge-preview`
入参 `target_requirement_key`（必填）、`merge_mode`（`union` 默认 / `replace`）。

出参（2026-09-16 实测，与文档一致）：

```json
{
  "source_id": 225877630925144064, "merge_mode": "union",
  "target": {"requirement_key": "REQ-000015", "requirement_name": "…", "current_version": 3},
  "next_version": 4,
  "groups": [{"module_key": "巡检任务", "module_name": "巡检任务",
              "added": [{"content": "…"}],
              "modified": [{"feature_key": "F-003", "before": "…", "after": "…",
                            "module_before": null, "module_after": "…"}],
              "deleted": [{"feature_key": "F-009", "content": "…"}],
              "kept": 6}],
  "summary": {"add": 0, "modify": 0, "delete": 0, "keep": 16, "active_after": 16},
  "overrides": [],
  "warnings": ["来源的功能行与目标需求完全一致，合并不会产生任何变更"]
}
```

- **`warnings` 必须展示**，而且**不只在 replace 模式出现**：实测 `union` 且无差异时
  也给了一条「完全一致，合并不会产生任何变更」。`replace` 模式则会给
  「合并后目标需求将失去 N 条现有功能」——**删除清单只在这里喊出来**。
- `groups` 的键是 `added` / `modified` / `deleted` / `kept`（`kept` 是**数字**）
- `overrides` 是**可直接回传的草稿**（配合 `feature_overrides` 实现人工微调）
- 错误：来源不存在 / 目标 REQ 不存在 → 404；来源非 `pending_review` → 409

### `POST /api/v1/reviews/submit`
Body：`{source_id, decision("approved"|"rejected"|"returned"), target_requirement_key?, merge_mode?, reviewer_name?, comment?, edited_requirement?, feature_overrides?}`

- `target_requirement_key` 非空 = **合并进既有 REQ**（产生新版本）；为空 = 新建 REQ
- 出参：`{decision, reviewer_id, status, version_no, requirement_key}`
- 409 = 冲突（来源已被处理 / 不存在）
- ⚠️ 这里的 `source_id` 用**字符串**传入（§1.1）

---

## 5. 能力 / 条件 / 标题（2026-09-15 新增）

### `GET /api/v1/requirements/{key}/capabilities`

```json
{
  "requirement_key": "REQ-000015", "requirement_name": "…", "current_version": 3,
  "capabilities": [{"feature_id": 225548242014699520, "capability_id": 225856650450305024,
                    "feature_key": "F-001", "feature_content": "…", "action": "创建",
                    "object": "巡检计划", "display_name": "创建 巡检计划",
                    "capability_status": "pending_confirmation",
                    "raw_text": "…", "confidence": null,
                    "review_status": "proposed", "decided_by": null}],
  "constraints": [{"raw": "按门店", "matched": false, "constraint_key": null}]
}
```

- `capabilities` 来自 `feature_capability`，**只含仍生效的功能**
- `constraints` 来自**当前版本的快照** —— 条件只存在于快照里
- `review_status` 展示时必须标（`proposed` = 待确认）
- ⚠️ **`feature_id` / `capability_id` 是雪花 number**（旧版示例写的 `1`/`2` 是误导）
- ⚠️ **实测的 `constraints` 条目里没有 `alias_hit` 键**（只有 `raw`/`matched`/`constraint_key`）——
  因为快照是**修复前**产生的，`alias_hit` 是「向前生效、不回填历史」。见 §8.1。

### `GET /api/v1/capabilities` / `GET /api/v1/constraints`
入参 `status`（`active`/`deprecated`/`pending_confirmation`）、`q`、`limit`。

**capabilities 项**（实测）：
`{id, action, object, display_name, status, created_by, origin_source_id, created_at, updated_at}`

> `origin_source_id` = 这条能力提案是哪个来源提的（批次 2 加的溯源；已确认的能力可能为 `null`）。
> ⚠️ `id` 是**雪花 number**（实测 `225865344202309632`），不是小整数。

**constraints 项**：⚠️ **形状仍未实测** —— `constraint_vocab` 是**空表**（2026-09-16 复跑仍为 0 行），
接口实返回 `{"items": []}`。按仓储代码，它比 capabilities 多一个 `aliases[]`，但**只是读代码得出的**。
前端接这块时先打一次看真实形状。见 §10-T4。

### `GET /api/v1/capabilities/{id}/streams`
按能力反查需求主线。入参 `constraint`（正式键或原文均可）、`review_status`、`limit`。

出参（**实测**）：`{"items":[{requirement_key, requirement_name, status, current_version, review_status, action, object, display_name}]}`

- ⚠️ 路径里的 `{id}` 是**雪花 ID**（`/capabilities/1/streams` 会 404，因为不存在 id=1）
- 能力不存在 → 404
- 加 `constraint` 过滤到「当前版本带该条件」的主线；条件不在当前版本 → 0 条
- 加 `review_status=confirmed` → 当前全库无 confirmed，返回 0 条（**如实反映，不是漏搜**）

### 裁决端点（**人工确认的唯一入口**）

| 端点 | Body | 作用 |
|---|---|---|
| `PATCH /api/v1/feature-capabilities` | `{feature_id, capability_id, status("confirmed"\|"dismissed")}` | 这条需求有没有这个能力 |
| `PATCH /api/v1/capabilities/{id}` | `{status("active"\|"deprecated"\|"pending_confirmation")}` | 这条能力本身成不成立 |
| `POST /api/v1/constraints/aliases` | `{alias, constraint_id}` | 把原始表达登记为别名 |

> **确认能力后它才参与后续匹配** —— 在此之前 AI 提议的能力对后续需求等于不存在。
> ⚠️ 这里的 `feature_id` / `capability_id` / `constraint_id` 同样建议按字符串传，避免精度问题。

### `GET /api/v1/requirements/{key}/titles`
`{"requirement_key": "…", "items":[{id, requirement_id, title, angle, capability_id, constraint_key, source, review_status, decided_by, created_by, created_at, highlight}]}`

**`highlight` 是这批标题的意义**：从不同标题点进去，内容不变、高亮不同。

```json
"highlight": {"feature_keys": ["F-001"], "kind": "capability", "capability_id": 2}
```
- `kind=capability` → 高亮 `feature_keys` 里的行
- `kind=constraint` → 高亮 `feature_keys`（当前版本带该条件的全部功能）；条件不在当前版本时 `feature_keys` 为空
- `kind=business_object` / `free` → `feature_keys` 为空（视为整条需求）

其余：`PATCH /api/v1/requirement-titles/{id}`（`{status}`）、
`POST /api/v1/requirements/{key}/titles`（`{title}`，人工新增）。

> ⚠️ **派生出的标题一律 `proposed`**，列表只该展示 `confirmed` 的 ——
> 否则会把机器起的名字当成正式叫法。
>
> ⚠️ **实测 `items` 是空的**：`requirement_title_candidate` 表 **0 行**（2026-09-16）。
> 也就是说这套能力的**真实形状从来没被运行时验证过**，`highlight` 的四种 kind 一个都没跑过。
> 前端目前也**没有调用这个端点**。见 §10-T4 / T5。

---

## 6. 对话（SSE）

`POST /api/v1/agent/chat/stream`，`text/event-stream`。

**事件名（2026-09-16 更正）**：实际是
**`session` / `step` / `artifacts` / `narrative` / `done` / `error`**。

⚠️ 旧版写的 `delta` / `message` **在后端根本不存在** —— 照旧文档写必然收不到任何内容。
前端实际处理的就是这 6 个（`app.js:570-580`、`723-729`）。

| 事件 | data |
|---|---|
| `session` | `{session_id, run_id}` |
| `step` | `{step, label}`（`label` 是中文，直接显示） |
| `artifacts` | `{artifacts: {extracted, analysis, risk, candidates, ...}}` —— **整包给**，不是增量 |
| `narrative` | `{t: "文本片段"}` 逐段拼接；另有 `{start: true}` 表示开始 |
| `done` | `{run_id}` |
| `error` | `{message}`（并发冲突时 message 是中文说明） |

- 同会话并发 → **409**，`detail.active_run` 带正在跑的 run。
  注意：真跑到 409 时服务端仍会推 `error` + `done` 两个事件（`agent_chat.py:333-334`）
- `GET /api/v1/agent/chat/{sid}/resumable` → `{"run": null}` 或 `{run_id, status, stage, steps_done, checkpoint_keys}`
- `POST /api/v1/agent/runs/{id}/resume` → SSE，跳过已算阶段（首个 `step` 的 label 是「已恢复上次分析，从断点继续…」）

**其余对话端点**（契约此前未列，前端在用）：`POST /api/v1/agent/chat`（非流式）、
`POST /api/v1/agent/chat/stream-with-files`（多文件）、`GET /api/v1/agent/chat/{sid}`、
`POST /api/v1/agent/runs/{id}/pause`、`GET /api/v1/agent/runs/{id}`。见 §10-T3。

---

## 7. 运维

`GET /api/v1/ops/outbox` → `{counts:{pending,processing,completed,dead_letter,discarded}, dead_letters[], consumer:{running, poll_count, processed_total, retried_total, dead_letter_total, last_active_at, interval_seconds, batch}}`

> ⚠️ **`consumer` 可能是 `null`**：它由 API 进程的 lifespan 提供，
> 测试客户端或未启用 outbox 时就是 `null`（实测就是 `null`）。
> 前端已按 null 处理（`app.js:1473`），重写时别漏。

其余：`POST /api/v1/ops/outbox/dead-letters/{event_id}/retry` 与 `/discard`。
⚠️ 这两个**没有鉴权**（见 `docs/current-state.md` §三 遗留 8），且 `event_id` 也是雪花 number。

---

## 8. 已知的坑

**8.1 `constraints[].alias_hit` 在旧数据里可能缺失**
后端 `_constraint_snapshot` 曾经只在 `matched` 分支给 `alias_hit`（已修，**向前生效、不回填历史**）。
旧版本快照里的未命中条目没有这个键。**读之前判空**（`c.alias_hit ? … : ''`）。

> 2026-09-16 复跑**抓到活的实例**：REQ-000015 的 v3 快照（生成于修复前 4 小时）
> 返回的是 `{"raw": "按门店", "matched": false, "constraint_key": null}` —— **没有 `alias_hit` 键**。
> 所以这条不是理论风险。

**8.2 `metadata` 的键是逐个长出来的**
`capability_match` 是 2026-09-15 批次 2 之后才有的；`suggestion` 是 2026-09-16 才有的，
而且**判不准时整个键不存在**。实测 4 条待办里没有一个拥有全部键。
**必须逐键容错**，不要一步取值到底。

**8.3 详情页的能力查询要容错**
`/capabilities` 对老需求可能返回空数组（它们建于能力模型之前）——
**空 ≠ 出错**，别让整页挂掉。前端当前用 `.catch(() => ({capabilities: [], constraints: []}))` 兜底。

**8.4 相似度要标口径**
`analysis.candidates[].similarity` 是模型给的原始相似度，而**是否判定为重复/关联由后端阈值决定**（strict：duplicate 0.80 / related 0.72）。
展示时带上口径（如「相似度 93%（严格模式重复阈值 80%）」），否则会重演「假数据冒充模型输出」。

**8.5 一条主线只有一个 `current`**
由数据库部分唯一索引保证。前端不要假设「version_no 最大的就是当前版」——
**要用 `status === 'current'`**（回滚后二者会不一致）。

**8.6 ID 类型按端点而异，别全局当字符串**（2026-09-16 新增）
见 §1.1。只有审核端点的 `source_id` 是字符串；其余是 number 且**今天只是恰好安全**。

**8.7 AI 提议的字段必须标出来**
`review_status=proposed`、`status=pending_confirmation`、`analysis.suggestion`、
`capability_match.capabilities[].matched=false` —— 这些都是**机器说的**，不是人定的。
展示时不标，人就会当结论用。

---

## 9. 怎么重跑这份契约验证

验证脚本的思路（不依赖浏览器）：

1. 从 `static/js/app.js` **提取实际读取的字段**（按渲染函数 grep `x.field`），
   **不要凭记忆列** —— 凭记忆就是自证循环
2. 用 `TestClient` 打真实库，逐个端点核对那些字段是否存在
3. 覆盖**每个分支的形状**（如 `matched: true/false` 两种条目）
4. **把响应里的值按 JSON 类型清点一遍**，重点看「大整数是 string 还是 number」——
   §1.1 那三条更正就是这么抓出来的

实测下来这一步能抓到「字段名对不上」「同一数组两种形状」「文档写错了事件名」这类问题，
而它们**在浏览器里才表现为空白或 undefined**，定位成本高得多。

> ⚠️ **这段目前还只是「思路」，脚本没有进仓库。** 2026-09-16 这次复跑用的是两个一次性脚本，
> 写在 `/tmp/api_contract_verify_20260916.py` 与 `/tmp/id_type_inventory.py` ——
> **`/tmp` 会被清掉，等于没留下。** 把它固化成 `scripts/verify_api_contract.py` 是 §10-T2。

---

## 10. 还没做的（任务清单）

> 按「做完之后谁受益」分两组：**T1–T5 是契约/前端侧**，T6–T7 是**会让契约再次变化**的实现侧。
> 每条都写了「怎么验」，与本文档其余部分同一套标准：**能实测的才算做完**。

### T1 · ID 序列化统一（🔴 优先级最高，会真实炸）

**现状**：只有 `/reviews/pending` 与 `/reviews/{id}/detail` 把 `source_id` 字符串化
（`repositories/requirement.py:335`），其余端点全是 JSON number。当前**恰好**因为
`worker=0 & seq=0` 而安全，但这件事已经真实发生过一次（同文件注释里有记录）。

**怎么做**：
1. 先定口径 —— 建议二选一，别混：
   (a) **全部雪花 ID 一律字符串**（与 §1 原意一致，但改动面大、是契约变更）；
   (b) **只把「会被前端拼进 URL 的 ID」字符串化**（`relations.id`、`capabilities.id`、
   `feature_id`、`capability_id`），改动面小、够用。
2. 出口在同一处收敛：给仓储层的行规范化函数加统一的 `_sid()`，**不要在每个路由里手工 `str()`**
   —— 现在正是「手工 str 了一处」造成的分裂。
3. `RequirementSubmitResponse.source_id` 目前是 `int | None`（`api/schemas/requirements.py:52`），
   一并改。
4. **怎么验**：把 `common/snowflake.py` 的 worker/seq 调成非 0 造一个 ID，
   跑一遍「提交 → 待办列表 → 审核提交」与「关系 PATCH」，确认没有 404/409。

> ⚠️ 这是**对外契约变更**，与 `current-state.md` §三 遗留 6 是同一条，动之前要说一声。

### T2 · 把验证脚本固化进仓库（🟢 半天）

**现状**：§9 只有思路，脚本没进仓库（这次的两个也是 `/tmp` 里的一次性脚本）。

**怎么做**：新建 `scripts/verify_api_contract.py`：
1. 从 `static/js/app.js` 正则提取渲染函数里读的字段名
2. `TestClient` 打真实库，逐端点核对「该字段在不在」
3. 顺带输出 §1.1 那张 **ID 类型表**（大整数是 string 还是 number）
4. 退出码非 0 = 契约与实现不一致 → 可以挂进 CI

**怎么验**：故意把 app.js 里一个字段名改错，脚本应该报出来。

### T3 · 补齐契约没覆盖的端点（🟢 一天）

**现状**：前端在调、但契约里没有的端点：

| 分组 | 端点 |
|---|---|
| 探活 | `GET /api/v1/health/db`、`GET /api/v1/health/llm`（后者返回 provider/model/embedding 配置与调用统计） |
| 会话 | `/api/v1/conversations` 全套（列表/新建/详情/消息/改名/删除/finalize） |
| 文档 | `/api/v1/documents`、`/documents/search`、`/documents/{id}`、`/documents/{id}/chunks`、`/documents/{id}/reindex` |
| 记忆 | `/api/v1/memory`（列表/新增/删除/context） |
| 审计 | `GET /api/v1/audit/events?limit=N` |
| 来源回放 | `GET /api/v1/sources/{source_id}/trace` |
| 审查 | `GET /api/v1/reviews/{source_id}/detail`（后端有，**前端没调**） |
| 提交 | `POST /api/v1/requirements/submit`、`POST /api/v1/requirements/ingest`（multipart） |
| 对话 | `POST /api/v1/agent/chat`、`/chat/stream-with-files`、`GET /chat/{sid}`、`POST /runs/{id}/pause`、`GET /runs/{id}` |
| 运维 | `POST /ops/outbox/dead-letters/{id}/retry`、`/discard` |
| 渠道 | `POST /api/v1/channels/feishu/webhook`（**唯一无鉴权端点**，见 current-state §四） |

**怎么做**：扩展 T2 的脚本，把上表也纳入核对；每个端点补一节字段表（含实测样例）。

### T4 · 三个「空的形状」要拿真实数据跑一遍（🟡 卡在数据）

| 形状 | 为什么是空的 | 怎么造数据 |
|---|---|---|
| `/api/v1/constraints` 的条目（`aliases[]`） | `constraint_vocab` 0 行 | 走「审核 → 条件未入词表 → 人工登记别名」链路（`POST /constraints/aliases`），或直接插一行词表 |
| `capability_match.constraints.matched[]` | 同上 | 同上；命中后才有 `alias_hit: true` 分支 |
| `/requirements/{key}/titles` 的 `highlight` 四种 kind | `requirement_title_candidate` **0 行** | 对一条有能力的 REQ 调 `POST /requirements/{key}/titles` 手工新增，再分别按 capability / constraint / business_object 造 |

**怎么验**：造完数据后把真实响应形状**回填进本文档对应小节**，并把 §10-T4 这条划掉。
**在拿到实测形状之前，前端不要照着本文档的示例写死。**

### T5 · 前端接线缺口（🟢 看前端排期）

| 缺的 | 说明 |
|---|---|
| `analysis.suggestion` 没渲染 | 后端已出「该新建还是追加」的建议（§4.2），`app.js` 里**完全没有这个词**。审核页应展示 action + 目标 REQ + 置信度，并标「AI 建议，需人工确认」 |
| `/requirements/{key}/titles` 没调用 | 数据 0 行 + 前端 0 调用 = 这个功能目前**对用户不存在** |
| `/reviews/{id}/detail` 没调用 | 后端有、前端没用（待办列表已带全量 `metadata`，所以可能确实不需要） |
| 需求库筛选下拉是空的 | `departments`/`sensitivity_levels` 实测是 `[]`，下拉选项由当前结果集聚合而来（无 facets 接口）。见 `current-state.md` §三 遗留 7 |

### T6 · 文档版本链的**写入路径**（🔴 会新增端点，契约要跟着改）

**现状**：方案批次 1+2 已落地（`4f6bf0d`）—— `document_stream` 表、`document_asset` 的
`stream_id/version_no/status`、分片的 `content_hash` + `plan_chunk_sync` 都在了。
但 **HTTP 层一个字都没变**：`repositories/document.py` 的 `list_documents`/`get_document`
是**显式列清单**，没有带新列 —— 实测响应里 `stream_id`/`version_no`/`status` **都不存在**。

也就是说：**数据层已就位，但它对前端完全不可见，且上传路径还在按 checksum 建新资产。**

**怎么做**（按 `docs/方案_文档版本链.md` 批次 3-5）：
1. 批 3：改上传路径 —— 按 `(file_name, content_type)` 找 `document_stream`，
   命中则 supersede 旧版本再插入新版本（**supersede 必须先于插入**，需求侧批次 3 在这里踩过）
2. 批 4-5：加读取端点（文档版本列表 / 两版分片 diff），复用 `chunk_diff.plan_sync`
3. 每加一个端点，**同步补进本文档 §3 或新增一节**，并把新增列加进 `_normalize_asset_row`

**怎么验**：同一文件名上传两次不同内容 → `document_stream` 仍 1 条、
`document_asset` 变 2 行且只有一条 `status='current'`、两次响应的 `version_no` 不同。

### T7 · F/G 批（版本链 DAG / revert + 乐观锁）（🟡 会改契约）

`current-state.md` §2.2 的 B1/B2。**为什么影响契约**：
- F 批要给 `/trace` 补 `merged_from` 字段 → §3 的 trace 小节要改
- G 批的 `lock_version` 现在**已经在 `/trace` 的 `requirement` 里返回了但是死列**（实测），
  乐观锁接上后，写端点会多出 409 冲突语义 → §4 的 `submit` 错误表要改

**怎么做**：见 `docs/方案_对话状态机与Git式版本管理.md` §3.3–3.5。
**做完后回来改本文档**，别让契约再次落后于实现。

---

## 附：本次复跑的原始证据

| 结论 | 证据 |
|---|---|
| 列表 14 字段 | `GET /api/v1/requirements` 实测返回 14 个键 |
| ID 类型分裂 | 清点 13 个端点：`source_id` 在审核端点是 string、在 relations/diff/features 是 number |
| `suggestion` 是新增键 | 4 条待办中仅最新 1 条有；另 3 条该键**完全不存在** |
| `constraints` 无 `alias_hit` | REQ-000015 v3 快照实测 3 条均无该键（快照生成于修复前 4 小时） |
| `/constraints`、`/titles` 为空 | 直查库：`constraint_vocab` 0 行、`requirement_title_candidate` 0 行 |
| 文档新列不可见 | `GET /api/v1/documents` 响应里无 `stream_id`/`version_no`/`status` |
| SSE 事件名 | `agent_chat.py` 的 `_sse(...)` 调用点：只有 session/step/artifacts/narrative/done/error |
| 测试基线 | `pytest -q` → **341 passed, 2 skipped**（与 `4f6bf0d` 记录一致） |
