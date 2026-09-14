# 方案：对话状态机 · 单对话并发隔离 · Git 式版本管理

> 状态：设计稿 v1（**未实施**） ｜ 生成日期：2026-09-14
> 事实源：当前代码。所有「现状」均标注 `文件:行号`，可逐条复核。
> 阅读顺序：§0 是你要的事实确认，§1–§3 是三件事的设计，§4 是建议的施工批次。

---

## 0. 先回答：现在有没有「可暂停、可继续」的状态机？

**没有。现在是「要么跑完，要么作废」，没有中间态。**

| 你以为有的 | 实际是什么 | 证据 |
|---|---|---|
| 对话有状态机 | 有，但那是**一次运行的生命周期**，不是可暂停的思考过程 | `agent_run.status CHECK IN ('running','completed','failed','cancelled')`（`migrations/003:34-49`）——**枚举里根本没有暂停态** |
| 暂停后能继续 | 不能。中断的 run 一律**从头重跑** | `_try_replay` 只认 `status == 'completed'`（`api/routes/agent_chat.py:235`），`cancelled`/`failed` 直接返回 None 走全量重算 |
| 思考过程不会清空 | **会被丢掉**。已算好的分析结果随异常一起消失 | `append_assistant_message` 只在管线**全部走完后**调用（`agent_chat.py:365-370`）；`except asyncio.CancelledError` 只写 run 状态、不落任何中间产物（`agent_chat.py:373-375`） |
| 图编排有断点恢复 | LangGraph **明确不走 checkpointer** | `workflows/state.py:39` 注释原文「不走 checkpointer」；`graphs.py:43,63` 是裸 `compile()`，`invoke` 不传 `thread_id` |
| 前端能续上 | 前端每次发送都生成**新的** `client_message_id`（`app.js:483`），所以连「已完成重放」都基本命中不了 | `_try_replay` 按 `(conversation_id, client_message_id)` 查（`chat.py:255-270`） |

**最疼的一点**：四个 LLM 步骤（extract→retrieve→analyze→risk）跑完了、正在流式输出叙事文本时客户端断开——那些**已经花掉 token 算好的 `pipeline` 会被整个丢弃**，库里只留一条空的 `cancelled` run。用户重发就是再花一遍钱重算。

---

## 1. 对话状态机与「暂停 / 继续」

### 1.1 目标

1. 客户端断开/暂停时，**已完成的步骤结果必须留下**（不能白算）
2. 用户能对一条未完成的运行点「继续」，**从断点接着跑**，而不是重算
3. 中断与恢复必须是**显式动作**，不能靠猜

### 1.2 设计

#### (a) 步骤级 checkpoint 落库

给 `agent_run` 增两列（迁移 `012`）：

```sql
ALTER TABLE agent_run
    ADD COLUMN IF NOT EXISTS stage TEXT NOT NULL DEFAULT 'queued',
    ADD COLUMN IF NOT EXISTS checkpoint JSONB NOT NULL DEFAULT '{}';

ALTER TABLE agent_run DROP CONSTRAINT IF EXISTS agent_run_status_check;
ALTER TABLE agent_run ADD CONSTRAINT agent_run_status_check
    CHECK (status IN ('running', 'paused', 'completed', 'failed', 'cancelled'));
```

- `stage`：`queued → extracting → retrieving → analyzing → assessing → narrating → done`
  即现在那个仅用于展示的 `steps` 数组（`agent_chat.py:285`）升级为**真实状态**
- `checkpoint`：已完成阶段的产物（`extracted` / `candidates` / `analysis` / `risk`），
  形如 `{"extracted": {...}, "candidates": [...], "analysis": {...}, "risk": {...}}`

**写点**：`_stream_chat_pipeline` 每个阶段结束时调用 `update_run(stage=…, checkpoint=…)`。
这样断开时最多丢「当前那一步」，之前的花费全部保住。

> 顺带修一个既有缺陷：`update_run` 目前**整体覆盖 `meta`**（`chat.py:213-238`，不是合并）。
> 新增的 `checkpoint` 单独成列正是为了避开这个坑；`meta` 的覆盖语义另开一条修复。

#### (b) 续跑：`_try_replay` 扩展为三级

把现在的「只认 completed」改成按状态分流：

| run 状态 | 行为 |
|---|---|
| `completed` | 全量重放（现状不变） |
| `paused` / `cancelled` / `failed` **且有 checkpoint** | **续跑**：跳过 `stage` 之前的步骤，从断点继续 |
| 无 checkpoint / 无 run | 从头跑 |

关键实现点：四个步骤的结果都在 `pipeline` 里，续跑时把它们**预填**进 pipeline，
只补跑缺失的部分。叙事（narrating）永远重跑——它便宜且用户可以接受换一种说法。

#### (c) 暂停与继续的入口

- `POST /api/v1/agent/runs/{run_id}/pause` → run 置 `paused`，SSE 在下一个阶段边界停下
- `POST /api/v1/agent/runs/{run_id}/resume` → 以该 run 为起点创建续跑（复用同一 run_id，
  置回 `running`），SSE 从中断的 stage 继续吐
- 前端：未完成的 run 在会话里显示为一张**「上次分析未完成 · 已算完 N/4 步」卡片**，带「继续」按钮

#### ⚠️ 1.3 必须坚持的取舍：**不要用文本推断「继续」**

会话里发一句「继续」就当续跑——这个思路**已经被证伪过一次**：本轮会话里我做过一个
「识别『需要』=确认提交」的功能，结果是用户打的一句确认语被当成了新需求，还往待办里
塞了一条垃圾，最后整体回退（`0f62f18`）。

**结论**：续跑必须是**按钮/端点**，不是意图识别。如果要支持文本，也只能是
「前端把这句话翻译成一次 API 调用」，判定逻辑仍留在 UI 层由用户可见地触发。

### 1.4 验收

- 断开重连后，`agent_run.checkpoint` 里有已完成的阶段产物，且不重跑这些阶段
  （用 LLM 调用次数断言：续跑时 `calls_total` 的增量应小于从头跑）
- 「继续」按钮在未完成 run 上可见、在已完成 run 上不可见

---

## 2. 单对话并发隔离（一次只思考一个问题）

### 2.1 现状：**后端零保障**

- 唯一的屏障是前端 `state.streaming` 这个 **JS 布尔量**（`app.js:22-27`），
  多标签页、多客户端、直接 curl 都能绕过；前端**连 `AbortController` 都没有**
- 后端**没有任何会话级锁**（全仓只有雪花 ID 与 LLM 统计两把锁，与会话无关）
- `agent_run` 上有 `uq_run_once (conversation_id, client_message_id)`（`003:47-49`），
  但那是**幂等去重**不是互斥：两个不同的 `client_message_id` 打同一对话，两条都会成功、并行跑

### 2.2 设计：把约束放进数据库

在 DB 层加**部分唯一索引**，让「同一对话最多一个活跃运行」成为不可绕过的事实：

```sql
CREATE UNIQUE INDEX IF NOT EXISTS uq_run_active_per_conversation
    ON agent_run (conversation_id)
    WHERE status IN ('running', 'paused');
```

- 并发插入第二个活跃 run → `23505` 唯一冲突 → 路由捕获后返回 **409**
- **为什么用 DB 约束而不是 asyncio.Lock**：锁是进程内的，多 worker 下形同虚设；
  唯一索引天然跨进程、跨实例（`FOR UPDATE SKIP LOCKED` 的 outbox 消费已是同一思路）

### 2.3 冲突时的用户体验（这一步不能省）

直接 409 很突兀。设计成：

- 409 的响应体带上**正在运行的那个 run**（id、stage、开始时间）
- 前端提示「上一个问题还在思考中（已完成 2/4 步）」，并给两个出路：
  - **等它跑完**（前端轮询该 run 状态）
  - **取消并改问新问题**（先 `pause`/`cancel` 旧 run，再提交）
- 这才是「单对话一次只处理一个问题」该有的样子：**互斥 + 可解释 + 有出路**

---

## 3. Git 式版本管理

### 3.0 现状：**已经完成约七成，缺的是三个「连接」**

已经有的（不必重建）：

| 能力 | 位置 |
|---|---|
| 版本快照（全量文本 + feature 级变更清单） | `requirement_version`（`001:58-73` + `005:27-29`） |
| 功能条目 + 稳定标识 + 变更史 | `requirement_feature.provenance`（`005:1-16`、`review.py:107-114`） |
| 任意两点 diff | `diff_by_requirement_key(from_version,to_version)`（`review.py:491-557`） |
| 「合并进既有 REQ」的完整链路 | `target_requirement_key` + `feature_overrides`（`commit_nodes.py:181-239`） |

**缺的三个连接**：

1. **模块维度缺失** → `sync_features` 按 `ordinal 位置`配对（`review.py:163-166`），
   中间插入一行会让其后所有行**级联误判为 modify**。合并语义最脆弱的地方在这里。
2. **合并闭环断了** → 合并能力齐备但**前端没有入口**（`app.js:794` 从不发
   `target_requirement_key`）；分析出的 `duplicate` 只用于显示徽章与「要不要人工审」
   （`decision_rules.py:12-31`），**不产出「该合并到哪个 REQ」的建议**；
   刚建的 `requirement_relation` 与合并**没有任何代码连线**（确认一条 `duplicates_of`
   不会触发任何合并）。
3. **版本链画不出合流** → `parent_version_id` 列存在但**永远为 NULL**
   （`commit_nodes.py:247` 硬编码），前端是降序平铺列表（`app.js:979-995`），
   即使并入了另一个 REQ 的内容，链上也看不出「合流」。

### 3.1 模块化：让 feature 有「模块」这一级

**不新建模块表**，而是给 feature 加模块标签——因为「模块」在业务上就是 feature 的分组，
而 `requirement_feature` 已经是最终描述的组成单元（一条 REQ 的正文 = active features 拼接，
`review.py:67-69`）。

```sql
ALTER TABLE requirement_feature
    ADD COLUMN IF NOT EXISTS module_key TEXT,      -- 稳定标识，如 M-login
    ADD COLUMN IF NOT EXISTS module_name TEXT;     -- 展示名，如「登录」
CREATE INDEX IF NOT EXISTS idx_feature_module ON requirement_feature (requirement_id, module_key);
```

数据从哪来：**抽取阶段天然有模块信息**——模型实测会输出
`{"id": "REQ-001", "module": "...", "text": "..."}` 这种结构（`extract_skill.py:26` 注释），
只是 `_flatten_item` 的 `_ITEM_TEXT_KEYS` 没取 `module`（`extract_skill.py:17-20`），
把它糊成了一条字符串。

具体改动：
1. 抽取提示词明确要求**两级结构** `[{module, items:[...]}]`，`ExtractedRequirement` 增
   `modules: list[Module]`（保留 `requirements` 扁平字段做兼容）
2. `_flatten_item` 不再丢 `module`（顺手修掉现在「所有值拼成一条」的降级行为）
3. 落库时写 `module_key/module_name`

**模块的粒度由人工可调**：`feature_overrides` 已支持人工逐条裁决，加一个「改模块」的 op 即可。

### 3.2 合并闭环：把「像」变成「怎么合」

四步把断掉的链路接上：

1. **分析给出候选**（已有）—— `analysis.candidates` 带着对方 `requirement_key` 与相似度
2. **审核页给出合并入口**（**新增**）：
   - 候选列表里每条带一个「合并进这个 REQ」按钮
   - 点开后展示**预合并预览**：按模块列出将「新增 / 修改 / 删除」哪些功能——这需要先把
     `sync_features` 改成**纯函数式的预演**（不写库，只算 diff），复用同一套匹配逻辑
3. **提交时带上参数**（链路已有，只是没人传）：
   `target_requirement_key` + `feature_overrides`（人工微调）
4. **确认后同步关系状态**（**新增**）：把 `requirement_relation` 里对应的 `duplicates_of`
   置 `confirmed` —— 让「人确认过这条关系」这件事与「真的合并了」对齐

#### 同时修掉 `sync_features` 的级联误判

把匹配键从 **ordinal 位置** 换成 **模块内内容**：

- 先按 `module_key` 分组（都无模块时退化为单组，行为与现在等价）
- 组内用 `content_hash`（已有这一列）做精确匹配：命中即 `keep`，未命中才是 `add`
- 现有 feature 中未被任何新条目命中的 → `delete`

这样「中间插入一行」不再级联改写后续所有条目。**这是合并场景的必要前提**——
现在的算法在「两个相似需求合并」时会把目标 REQ 的既有功能大面积误改。

### 3.3 版本链 DAG 与可视化

#### (a) 让版本真的成「链」

```sql
ALTER TABLE requirement_version
    ADD COLUMN IF NOT EXISTS merged_from_requirement_key TEXT,
    ADD COLUMN IF NOT EXISTS merged_from_version_no INT;
```

- `parent_version_no`（已有）→ **同一条 REQ 内的前驱**（实线）
- `parent_version_id`（已有但恒 NULL）→ 暂不使用，或与 `parent_version_no` 二选一
- `merged_from_*`（新增）→ **合并来源**：这次版本引入了哪个 REQ 的哪个版本（虚线）

`commit_requirement_node` 在合并路径（`target_key` 非空）时写入 `merged_from_*`。
现在这条信息**完全丢失**——`diff_payload` 里连 `target_requirement_key` 都没回显。

#### (b) 前端画出来

把「版本历史」从降序平铺列表改成一棵**纵向时间轴**：

- 每个版本一个节点，左对齐按 `version_no` 排列
- `parent_version_no` 连**实线**（相邻版本）
- `merged_from_*` 连**虚线**到被并入 REQ 的对应版本（跨 REQ，需要 trace 端点补返回
  `merged_from` 与对方的 key/name）
- `change_type` 用不同色点：`new` / `add` / `modify` / `delete`
- 点节点展开该版本的 `feature_changes`（已有数据）

后端 `trace_by_requirement_key` 需补两件事：SELECT 里带出 `merged_from_*`，
并在返回里附上被合并方的 `requirement_key` / `requirement_name`（前端画虚线要有落点）。

#### (c) 顺带修掉两个小缺陷

- **前端 diff 只能看相邻版本**：`/diff` 调用不带参数（`app.js:945`），
  而后端 `from_version`/`to_version` 是支持的（`requirements.py:43-57`）→ 加两个下拉
- **`master.status` 的 `archived`/`deleted` 从未被写入**（`001:51-52`）→ 要么接上归档功能，
  要么从枚举里去掉（避免又一处「预留了但没人用」）

### 3.4 回滚（Git 有的，这里没有）

需求侧没有「撤销」入口。建议做成 **revert 而不是 reset**（保持 append-only，不改历史）：

- `POST /api/v1/requirements/{key}/revert`，body 指定目标版本号
- 实现：把目标版本的 features 作为新的目标集合，走一次 `sync_features` 产出新版本，
  `change_type='modify'`，`change_summary` 写明「回滚到 V{n}」
- 好处：历史仍是线性的、可审计；坏处：版本号会增长（与 Git revert 一致）

### 3.5 并发（`lock_version` 是死的）

`lock_version` 被写入但**从未被比较**（`commit_nodes.py:279-284` 写，
`master_repo.save` 的注释说「调用方应基于 lock_version 校验」但没人做）→
并发合并是 **last-write-wins**。

合并闭环上线后这个问题会立刻暴露（两个审核人同时合并进同一个 REQ）。
修法：`commit_requirement_node` 的 master 更新加 `WHERE id = :id AND lock_version = :expected`，
不匹配则抛「该需求已被他人修改，请重新审核」。

---

## 4. 建议的施工批次

按「独立可验证、风险递增」排序：

| 批次 | 内容 | 依赖 | 风险 |
|---|---|---|---|
| **A** | **单对话并发隔离**（§2）：唯一索引 + 409 + 前端提示与出路 | 无 | 低。纯约束，不改现有流程 |
| **B** | **checkpoint 落库**（§1.2a）：stage + checkpoint 两列，各阶段写回。**只做「不丢结果」，不做续跑** | A | 低。只增写入，不改读取路径 |
| **C** | **续跑**（§1.2b/c）：`_try_replay` 三级分流 + pause/resume 端点 + 前端卡片 | B | 中。要保证续跑不重复计费/不写重 |
| **D** | **模块化**（§3.1）：抽取两级结构 + feature 加模块列 + 落库 | 无 | 中。动抽取 schema，会影响既有 feature |
| **E** | **合并闭环**（§3.2）：预合并预览 + 审核页入口 + 关系状态联动 | D | 中高。先修 `sync_features` 的匹配键再开入口 |
| **F** | **版本链 DAG**（§3.3）：merged_from 两列 + trace 补字段 + 前端时间轴 | E | 中。纯展示，但依赖 E 的数据 |
| **G** | **revert + lock_version 乐观锁**（§3.4/3.5） | E | 中 |

**A 和 B 建议先做**：两者都只增不改、互不依赖 E/F 的数据，而且 B 直接止血——
「花掉 token 算完的四步因为断开而全丢」是当前最实际的浪费。

---

## 5. 明确不做（以及为什么）

- **不做「文本推断继续」**：见 §1.3，已被证伪过一次，代价是往用户数据里写垃圾。
- **不给 LangGraph 加 checkpointer**：流式聊天走的是 `agents_nodes` 的**直调**
  （`agent_chat.py` 根本没用 `analysis_graph`），给图加 checkpointer 覆盖不到聊天流；
  而且现有 `ctx` 里塞了 repo 与 session（`state.py:39` 注释明说「允许非序列化」），
  要持久化得先把这层重构掉。用 `agent_run.checkpoint` 这一层更轻、也够用。
- **不做分支（branch）**：需求治理的语义是「线性演进 + 合并」，分支模型（多人各改一份再合）
  在这里没有真实场景；真要做，成本远高于收益。
- **不改 `requirement_version_source.relation_type` 的死枚举**：`related`/`conflict`
  已由 `requirement_relation`（`009`）承担，那张表的 CHECK 值保留但不再启用——
  已经在 `009` 的注释里写明原因。
