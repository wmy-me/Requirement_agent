# 前端接口契约（需求治理域）

> **给重写前端的人看的。** 只写「后端实际返回什么」，不写渲染。
> 字段名以**实测**为准（2026-09-15 逐字段核对过，方法见 §9）。
>
> 为什么要这份文档：字段名对不上是重写前端最常见的坏法，而它**跑起来才炸**。
> 实测中就抓到过一个——`constraints[].alias_hit` 在后端两个分支里形状不一致
> （见 §8.1）。

---

## 1. 全局约定

| 约定 | 说明 |
|---|---|
| **雪花 ID 一律用字符串** | `source_id` 等超过 `2^53`，用 JS `Number` 承载会精度丢失。**后端已做字符串化，前端不要再 `Number()` 回去** |
| 列表一律 `{"items": [...]}` | 不是裸数组 |
| 时间 | ISO 8601 带时区（`as_display_iso`），可直接 `new Date()` |
| 错误 | 404 = 不存在；409 = 状态冲突（detail 是中文说明）；422 = 参数不合法 |
| 日期筛选的 `to` | 前端要补 `T23:59:59`，否则当天数据会被排除 |

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

> ⚠️ **只有 `confirmed` / `active` 是「人工确认过」的。** `proposed` /
> `pending_confirmation` 是 AI 提议，展示时必须标出来 —— 否则人会把模型猜的
> 当成已确认的（项目此前反复踩过「假数据冒充模型输出」）。

---

## 2. 需求库（列表页）

### `GET /api/v1/requirements`

入参：`q, channel, status, requester, department, business_domain, sensitivity_level, submitted_from, submitted_to, has_version_ge, limit(1-500, 默认100)`

出参 `{"items": [...]}`，每项 **10 个字段**：

| 字段 | 类型 | 备注 |
|---|---|---|
| `requirement_key` | string | `REQ-000015` |
| `requirement_name` | string | |
| `final_requirement` | string | 当前 active 功能的拼接 |
| `status` | string | |
| `business_domains` | string[] | **数组**；另有兼容字段 `business_domain` 取首个 |
| `current_version` | number | |
| `feature_count` | number | |
| `source_types` | string[] | |
| `requester_names` | string[] | |
| `latest_source_submitted_at` | string\|null | |

### `GET /api/v1/requirements/export`
同一套入参，返回 CSV（UTF-8 BOM），可直接 `window.open`。

---

## 3. 详情页

前端并发拉 5 个端点（+ 能力 1 个）。

### `GET /api/v1/requirements/{key}/versions`
`{"items":[{id, requirement_id, parent_version_id, parent_version_no, version_no, version_title, change_type, requirement_snapshot, change_summary, diff_payload, feature_changes, created_by, reviewed_by, created_at}]}`

### `GET /api/v1/requirements/{key}/features`
入参 `at_version`（可选）、`include_deleted`。
`{"items":[{id, requirement_id, feature_key, content, status, ordinal, origin_source_id, origin_requirement_key, origin_version_no, removed_version_no, provenance, module_key, module_name}]}`

> `module_name` 就是详情页的 📦 标签；为 `null` 表示该功能不归属任何模块。

### `GET /api/v1/requirements/{key}/diff`
入参 `from_version` / `to_version`（都可空，默认相邻两版）。
出参 `{requirement_key, from_version, to_version, added[], removed[], modified[], unchanged}`

- `added[]` / `removed[]`：feature 行
- `modified[]`：`{feature_key, before, after}`
- `unchanged`：**数字**（不是数组）

> 实测自洽：REQ-000015 的 `v1→v3` 得 `added=4`（v2 引入 1 + v3 引入 3），
> `features?at_version=1` 得 12 条（正是 v1 当时的功能数）。

### `GET /api/v1/requirements/{key}/trace`
需求主体 + 逐版本快照 + 每版来源链。

### `GET /api/v1/requirements/{key}/relations`
`{"items":[...]}`，**双向**（我指向别人 + 别人指向我），每项：
`{id, subject_requirement_key, target_requirement_key, relation_type, reason, similarity, source_id, status, created_by, decided_by, created_at, updated_at, direction, other_requirement_key, other_requirement_name}`

- `direction`：`outgoing`（本需求 → 对方）/ `incoming`
- `relation_type`：`duplicates_of` / `related` / `conflict` / `depends`

### `PATCH /api/v1/requirements/relations/{relation_id}`
Body `{"status": "confirmed" | "dismissed"}`。**不接受改回 `proposed`**（撤回需重新分析）。404 不存在。

---

## 4. 审核页

### `GET /api/v1/reviews/pending?limit=N`
`{"items":[...]}`，每项含 `source_id`(字符串) / `original_text` / `processing_status` / **`metadata`**。

**前端要读的都在 `metadata` 里**：

| 路径 | 内容 |
|---|---|
| `metadata.extracted` | `{requirement_title, summary, business_object, requirements[], modules[], ...}` |
| `metadata.analysis` | `{duplicate, related, conflict, independent, reasoning, candidates[]}` |
| `metadata.risk` | `{quality_risk, change_risk, technical_impact_risk, confidence, source}` |
| `metadata.capability_match` | **批次 2 起有**，见 §4.1 |

> ⚠️ **`metadata.capability_match` 只在批次 2（2026-09-15）之后提交的待办里有。**
> 更早的待办没有这个键 —— 前端必须容错（`meta.capability_match || {}`）。

#### 4.1 `metadata.capability_match` 形状

```json
{
  "business_object": "员工数据",
  "capabilities": [
    {"raw_text": "…", "action": "导出", "object": "Excel",
     "matched": true,  "capability_id": 1, "display_name": "导出 Excel"},
    {"raw_text": "…", "action": "查询", "object": "在职状态",
     "matched": false, "proposed": true, "capability_id": 9, "status": "pending_confirmation"}
  ],
  "constraints": {
    "matched":   [{"raw": "按部门维度筛选", "constraint_id": 1, "constraint_key": "按部门筛选", "alias_hit": true}],
    "unmatched": [{"raw": "按区域层级导出"}]
  },
  "summary": {"capability_total": 2, "capability_matched": 1, "capability_proposed": 1,
              "constraint_matched": 1, "constraint_unmatched": 1}
}
```

- `matched: true` → 命中已确认的词表
- `matched: false` → 新提案（`status: pending_confirmation`，**不参与后续匹配**）
- `constraints.unmatched` 里是**没入词表的条件原文**，要单独展示让人工决定

### `GET /api/v1/reviews/{source_id}/merge-preview`
入参 `target_requirement_key`（必填）、`merge_mode`（`union` 默认 / `replace`）。

出参：

```json
{
  "source_id": 123, "merge_mode": "union",
  "target": {"requirement_key": "REQ-000015", "requirement_name": "…", "current_version": 2},
  "next_version": 3,
  "groups": [{"module_key": "巡检任务模块", "module_name": "巡检任务模块",
              "added": [{"content": "…"}],
              "modified": [{"feature_key": "F-003", "before": "…", "after": "…",
                            "module_before": null, "module_after": "…"}],
              "deleted": [{"feature_key": "F-009", "content": "…"}],
              "kept": 4}],
  "summary": {"add": 3, "modify": 1, "delete": 0, "keep": 9, "active_after": 12},
  "overrides": [...],
  "warnings": ["replace 模式：合并后目标需求将失去 2 条现有功能"]
}
```

- **`warnings` 必须展示**：`replace` 模式造成的删除只在这里喊出来
- `overrides` 是**可直接回传的草稿**（配合 `feature_overrides` 实现人工微调）
- 错误：来源不存在 / 目标 REQ 不存在 → 404；来源非 `pending_review` → 409

### `POST /api/v1/reviews/submit`
Body：`{source_id, decision("approved"|"rejected"|"returned"), target_requirement_key?, merge_mode?, reviewer_name?, comment?, edited_requirement?, feature_overrides?}`

- `target_requirement_key` 非空 = **合并进既有 REQ**（产生新版本）；为空 = 新建 REQ
- 出参：`{decision, reviewer_id, status, version_no, requirement_key}`
- 409 = 冲突（来源已被处理 / 不存在）

---

## 5. 能力 / 条件 / 标题（2026-09-15 新增）

### `GET /api/v1/requirements/{key}/capabilities`

```json
{
  "requirement_key": "REQ-000015", "requirement_name": "…", "current_version": 3,
  "capabilities": [{"feature_id": 1, "capability_id": 2, "feature_key": "F-001",
                    "feature_content": "…", "action": "创建", "object": "巡检计划",
                    "display_name": "创建 巡检计划", "capability_status": "active",
                    "raw_text": "…", "confidence": null,
                    "review_status": "proposed", "decided_by": null}],
  "constraints": [{"raw": "按门店", "constraint_key": null, "matched": false, "alias_hit": false}]
}
```

- `capabilities` 来自 `feature_capability`，**只含仍生效的功能**
- `constraints` 来自**当前版本的快照** —— 条件只存在于快照里
- `review_status` 展示时必须标（`proposed` = 待确认）

### `GET /api/v1/capabilities` / `GET /api/v1/constraints`
入参 `status`（`active`/`deprecated`/`pending_confirmation`）、`q`、`limit`。

**capabilities 项**（实测）：
`{id, action, object, display_name, status, created_by, origin_source_id, created_at, updated_at}`

> `origin_source_id` = 这条能力提案是哪个来源提的（批次 2 加的溯源；已确认的能力可能为 `null`）。

**constraints 项**：⚠️ **形状未实测** —— 当前 `constraint_vocab` 是空表（未命中的条件
按设计不入词表）。按仓储代码，它比 capabilities 多一个 `aliases[]`，但**这一条只是
读代码得出的，没有运行时验证**。前端接这块时先打一次看真实形状。

### `GET /api/v1/capabilities/{id}/streams`
按能力反查需求主线。入参 `constraint`（正式键或原文均可）、`review_status`、`limit`。

出参（**实测**）：`{"items":[{requirement_key, requirement_name, status, current_version, review_status, action, object, display_name}]}`

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

---

## 6. 对话（SSE）

`POST /api/v1/agent/chat/stream`，`text/event-stream`，事件类型：
`session` / `step` / `delta` / `message` / `done` / `error`。

- 同会话并发 → **409**，`detail.active_run` 带正在跑的 run
- `GET /api/v1/agent/chat/{sid}/resumable` → `{"run": null}` 或 `{run_id, status, stage, steps_done, checkpoint_keys}`
- `POST /api/v1/agent/runs/{id}/resume` → SSE，跳过已算阶段

---

## 7. 运维

`GET /api/v1/ops/outbox` → `{counts:{pending,processing,completed,dead_letter,discarded}, dead_letters[], consumer:{running, poll_count, processed_total, last_active_at, interval_seconds, batch}}`

---

## 8. 已知的坑

**8.1 `constraints[].alias_hit` 在旧数据里可能缺失**
后端 `_constraint_snapshot` 曾经只在 `matched` 分支给 `alias_hit`（已修，**向前生效、不回填历史**）。
旧版本快照里的未命中条目没有这个键。**读之前判空**（`c.alias_hit ? … : ''`）。

**8.2 `metadata.capability_match` 是新增的**
2026-09-15 之后的待办才有。更早的没有该键，**必须容错**。

**8.3 详情页的能力查询要容错**
`/capabilities` 对老需求可能返回空数组（它们建于能力模型之前）——
**空 ≠ 出错**，别让整页挂掉。前端当前用 `.catch(() => ({capabilities: [], constraints: []}))` 兜底。

**8.4 相似度要标口径**
`analysis.candidates[].similarity` 是模型给的原始相似度，而**是否判定为重复/关联由后端阈值决定**（strict：duplicate 0.80 / related 0.72）。
展示时带上口径（如「相似度 93%（严格模式重复阈值 80%）」），否则会重演「假数据冒充模型输出」。

**8.5 一条主线只有一个 `current`**
由数据库部分唯一索引保证。前端不要假设「version_no 最大的就是当前版」——
**要用 `status === 'current'`**（回滚后二者会不一致）。

---

## 9. 怎么重跑这份契约验证

验证脚本的思路（不依赖浏览器）：

1. 从 `static/js/app.js` **提取实际读取的字段**（按渲染函数 grep `x.field`），
   **不要凭记忆列** —— 凭记忆就是自证循环
2. 用 `TestClient` 打真实库，逐个端点核对那些字段是否存在
3. 覆盖**每个分支的形状**（如 `matched: true/false` 两种条目）

实测下来这一步能抓到「字段名对不上」「同一数组两种形状」这类问题，
而它们**在浏览器里才表现为空白或 undefined**，定位成本高得多。
