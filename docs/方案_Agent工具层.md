# 方案：Agent 工具层

> 状态：⚠️ **批次 1 的代码已于 2026-09-16 回退**（实现出来后发现分层错了，见下）。
> 批次 2/3 未动。写于 2026-09-16。参照对象：`/home/wangmengyang/Software/CowAgent`（见 §1.3）。
>
> ### 为什么回退
>
> 批次 1 实现成「**直接薄封装 Repository**」—— 而按四层设计，只读工具应该封装
> **Application Service / Query Service**，不是 Repository。这个错误是
> `docs/分析_工具分层现状与越层调用.md` 的盘点发现的（该文 §3.4）。
>
> 同时发现它「**看起来被测试覆盖、实际没有**」：11 条单测只覆盖注册表与 schema，
> **没有一个测试调用真实工具的 `execute`** —— 实测把底层方法改个名，
> 22 条测试**全绿**而工具已经坏了。这种状态比明显的死代码更危险。
>
> **所以删掉重建，而不是修补。** 本文的设计分析（§1–§6）仍然有效，会作为重建的依据；
> **§7 保留作记录**（含三次设计偏差与三个实测发现，都是有用的知识）。
>
> 重建时的分层依据：`docs/分析_工具分层现状与越层调用.md`。

---

## 0. 三句话结论

1. **系统里现在没有任何「工具」概念** —— 无注册表、无 schema、`LLMProvider`
   **不支持 function calling**。所有 Agent 都是「prompt 进 → JSON 出」的单轮模式，
   检索等步骤是**编排里写死的**，模型看不见也选不了。
2. **工具层本身很薄，难的是循环外围的三层防护**（消息完整性 / 上下文预算 / 失效兜底）。
   但**这三层只有走到第三步才需要** —— 前两步不碰推理链路，没有失败模式。
3. **本项目有一条别的项目没有的硬约束**：**工具不能写库**。裁决（审核 / 合并 / 回滚）
   是**人的职责**，给模型等于绕开整套评审机制。所以工具层是「**只读 + 一个提交待审的入口**」。

---

## 1. 现状

### 1.1 Agent 与能力的实际接线

| Agent | 能力 | 数据从哪来 |
|---|---|---|
| `ExtractAgent.extract(text, …)` | 抽取结构化需求 | 只有入参文本 |
| `RetrievalAgent.retrieve(query, limit)` | 语义检索历史需求 | **编排调它**，结果再喂给下一个节点 |
| `AnalyzeAgent.analyze(extracted, historical)` | 判重复/关联/冲突 | `historical` 是**上一节点传进来的** |
| `RiskAgent.assess(extracted)` | 风险评级 | 只有抽取结果 |

关键在 `workflows/agents_nodes.py`：

```python
def retrieve_node(state):
    candidates = RetrievalAgent().retrieve(query, limit=5)   # ← 检索是流程写死的
    return {"candidates": candidates}
```

**模型从头到尾没有选择权**：查什么、查几条、要不要再查一层，全在编排里。

### 1.2 后端已有的能力（候选工具的来源）

散落在服务层与仓储层，**全部是确定性函数**，与 LLM 无关：

| 能力 | 落点 |
|---|---|
| 语义检索需求 | `RetrievalService.search(query, limit, filters)` |
| 需求详情 / 正文 | `RequirementMasterRepository.get_by_key` + `join_active_features` |
| 某版本的功能集 | `RequirementFeatureRepository.list_by_requirement_key(at_version=)` |
| 版本溯源 | `RequirementVersionRepository.trace_by_requirement_key` |
| 能力与条件 | `FeatureCapabilityRepository.list_for_requirement` |
| 按能力反查主线 | `FeatureCapabilityRepository.search_streams` |
| 需求关系 | `RequirementRelationRepository.list_for_requirement` |
| 文档分片检索 | `DocumentAssetRepository.search_chunks(query, limit)` |
| 待办列表 | `RequirementSourceRepository.list_by_status` |
| **提交待审** | `RequirementService.submit_requirement(source)` |

### 1.3 参照对象 CowAgent 的做法（结论摘要）

它的工具层分八层，但**核心契约极简**：

```python
class BaseTool:
    name: str = ""              # 类属性
    description: str = ""       # 给模型看的自然语言
    params: dict = {}           # JSON Schema
    parallel_safe: bool = False

    def execute(self, params) -> ToolResult:   # ← 唯一必须实现的
        raise NotImplementedError
```

**加一个工具 = 2 个文件 + 1 行注册**（`tools/<name>/<name>.py` + `__init__.py`，
再到 `tools/__init__.py` 的 `__all__` 加一行）。三条值得直接借：

- **注册表存「类」不存「实例」** → 每次调用新建实例，工具天然无状态、可并发
- **JSON Schema 是唯一 schema 方言** → MCP 的 `inputSchema` 原样透传，适配层只有 33 行
- **权限 / 截断 / 超时都不在基类里** → 各自独立的策略模块与共享 util

**但它的产品形态与本项目相反**：那是个能自由读写文件、执行命令的编码 Agent；
本项目是**需求治理**，一切写入必须经人工审核。**不能照搬的是「工具能写库」这件事**。

---

## 2. 三个必须先回答的问题

### 2.1 工具给谁用？（决定要不要做 function calling）

| 方案 | 含义 | 代价 |
|---|---|---|
| **(a) 本系统 Agent 自己用** | Agent 在推理中决定查什么 | **要动 `LLMProvider` + 写 agent loop**，还要那三层防护 |
| **(b) 对外暴露给别的 Agent** | 走 MCP（就是被删掉的那个方向） | 只做注册表 + 序列化，但**要鉴权**（而现在全站无鉴权） |
| **(c) 两者都要** | 注册表共用，消费方不同 | 先做注册表，两条消费路径各自接 |

**注册层是共用的，差别只在「谁来调」。** 所以可以先做注册表，把 (a)/(b) 押后。

### 2.2 工具能不能写？

**建议：写成硬约束 —— 工具只读，唯一写入口是 `submit_requirement`（进待审）。**

理由是这个项目反复守的一条线：

> 「审核通过后才生成 REQ」「**不提供绕过评审的直接写主表方法**」（`tools/__init__.py` 的原则，
> 已被删掉的那个包留下的最有价值的东西）
> 「只有 `confirmed` / `active` 是人工确认过的」

如果给模型一个 `merge_into_requirement` 或 `approve_review` 工具，**整套人工评审就形同虚设**。
所以裁决类端点（审核 / 合并 / 回滚 / 能力裁决 / 标题裁决）**一律不提供工具**。

> 注意 `submit_requirement` 本身是安全的：它只落一条 `requirement_source`（状态 `received`），
> 后续分析由后台任务补，**最终仍要人审**。它正是「Agent 想写入时该走的那条路」。

### 2.3 schema 是手写还是从类型注解生成？

CowAgent 用**类属性 + 手写 JSON Schema**。本项目已有 pydantic，可以**从类型注解生成**。

手写的代价是「注释与实现会漂移」；生成的代价是**描述文字（给模型看的）没法从类型推出来**，
仍要手写 docstring。折中：**参数结构生成、描述手写**（用 docstring）。这一步可以在实施时定。

---

## 3. 设计

### 3.1 契约

照 CowAgent 的形状，但**去掉它的产品绑定**（它基类里的 `renders_own_cards` /
`emit_event` 是给它自己的 SSE 卡片 UI 用的，本项目不需要）：

```python
# src/requirement_agent/tools/base.py
@dataclass(frozen=True, slots=True)
class ToolResult:
    """工具返回。`result` 给模型读；`display` 给人看（可选）。"""
    status: Literal["success", "error"]
    result: object
    display: str | None = None

class BaseTool:
    name: str                       # 注册键，也是给模型看的名字
    description: str                # 给模型看的说明（**写清楚什么时候不该用**）
    params: dict[str, object]       # JSON Schema
    read_only: bool = True          # 本项目里恒为 True（见 §2.2）

    def execute(self, params: dict) -> ToolResult: ...
    def get_json_schema(self) -> dict: ...   # 绑定实例，允许运行期变形
```

### 3.2 注册表

```python
# src/requirement_agent/tools/registry.py
TOOLS: dict[str, type[BaseTool]] = {}      # **存类，不存实例**

def register(cls): ...                     # 装饰器，或靠 tools/__init__.py 的 __all__ 扫描
def create(name: str) -> BaseTool: ...     # 每次新建实例
def list_schemas() -> list[dict]: ...      # 给 LLM 的工具列表 / 给 MCP 的工具清单
```

### 3.3 第一批工具（建议 10 个，全部只读 + 1 个提交）

| 工具名 | 干什么 | 落点 | 参数 |
|---|---|---|---|
| `search_requirements` | 按语义找相似需求 | `RetrievalService.search` | `query`, `limit` |
| `get_requirement` | 取一条需求的正文与当前版本 | `master_repo` + `join_active_features` | `requirement_key` |
| `get_features` | 取某版本的功能明细（含模块） | `list_by_requirement_key` | `requirement_key`, `at_version?` |
| `trace_requirement` | 版本溯源（每版来源链） | `trace_by_requirement_key` | `requirement_key` |
| `list_relations` | 该需求的关联/重复/冲突边 | `relation_repo.list_for_requirement` | `requirement_key` |
| `get_capabilities` | 该需求的能力与条件 | `feature_capability_repo` + 版本快照 | `requirement_key` |
| `find_streams_by_capability` | 按能力反查哪些主线用到它 | `feature_capability_repo.search_streams` | `capability` 或 `constraint` |
| `search_documents` | 在文档分片里检索 | `document_repo.search_chunks` | `query` |
| `list_pending_reviews` | 看待办（**只给标题与 key，不给全文**） | `source_repo.list_by_status` | `limit` |
| `submit_requirement` | **提交一条新需求进待审**（唯一写入口） | `RequirementService.submit_requirement` | `text`, `requester` |

> **为什么 `list_pending_reviews` 只给标题与 key**：待办里有大量原文与模型分析，
> 那是**给审核人看的**；给 Agent 全量等于让它替人做判断。要详情就再调一次 `get_requirement`。

### 3.4 三层防护（**只有第三步才需要**）

| 防护 | 解决什么 | 具体做法（借 CowAgent 的经验值） |
|---|---|---|
| **消息完整性** | 每个 `tool_use` 必须有配对的 `tool_result`，否则下一轮 provider 直接 400 | `finally` 块**无条件**补齐；另加 sanitizer 给孤儿调用合成结果 |
| **上下文预算** | 工具结果累积撑爆 context | 裁剪**只在循环开始前做一次**（中途裁会把当前 run 的 tool_use/result 拆散 → 模型陷入重复调用）；以**完整轮次**为单位 |
| **失效兜底** | 模型反复用同样参数调同一个工具 | 四条阈值：同参连续调用 ≥5 停、同参连续失败 ≥3 停、同工具失败 ≥6 停、≥8 中止整个 run。**非致命的停止变成给模型的建议**，不是抛异常 |

### 3.5 与现有编排的关系

**不替换现有四步图**（extract → retrieve → analyze → risk）。那是**确定性流程**，
每一步的输入输出都被测试钉着（341→412 条测试大半在这条链上）。

工具层是**加法**：让 Agent 在需要时能自己多查一层（比如「这条需求和哪条主线重复？
先看看那条主线的能力」）。**第三步之前，编排一行不用改。**

---

## 4. 分批实施

| 批 | 内容 | 风险 | 验证 |
|---|---|---|---|
| **1** | ⚠️ **已实现但已回退**（2026-09-16）：当时做成了「薄封装 Repository」，分层错了。**重建时要封装 Service/Query** | — | 重建的验收必须包含**真实 `execute` 的测试**（旧版缺这一块，会静默腐烂） |
| **2** | **接线到现有 agents**：工具清单降级成 prompt 里的说明文本，模型「请求调用」→ 编排执行 → 结果回灌 | 低（不改 provider） | 拿一条真实需求跑：模型主动多查了能力/版本，且输出仍能解析 |
| **3** | **真 function calling**：`LLMProvider` 支持 `tools`/`tool_calls` + agent loop + 三层防护 | **高**（动推理链路） | 需要 `pytest` 之外的验证：连续多轮工具调用、失败重试、超长结果截断 |

**批次 1–2 全程不碰 `LLMProvider`**，可以随时停。有风险的是批次 3。

---

## 5. 待你拍板

**① 工具给谁用？**（§2.1）本系统 Agent / 对外 MCP / 两者都要。
这决定批次 3 做不做、以及要不要先解决鉴权（对外暴露必然需要，而**全站现在无鉴权**）。

**② 「工具不能写库」这条硬约束，你认吗？**（§2.2）
我建议认，而且写进契约（`read_only` 恒真 + 一条测试守着）。
如果你希望 Agent 能直接改需求，那要重新讨论——**那等于绕开整套评审机制**。

**③ schema 手写还是从类型注解生成？**（§2.3）

**④ 第一步走多远？** 只做注册表（批次 1），还是一路做到接线（批次 1+2）？

---

## 6. 明确不做（以及为什么）

- **不给裁决类能力做工具**（审核 / 合并 / 回滚 / 能力裁决 / 标题裁决）—— 见 §2.2。
- **不替换现有四步编排** —— 它是确定性流程且被测试钉着；工具层是加法（§3.5）。
- **不做并行工具调用** —— CowAgent 的 `parallel_safe` 是为「多个独立慢任务」设计的，
  本项目工具全是本地 DB 查询（毫秒级），并行的收益不抵复杂度。
- **不做子 Agent / 工具内嵌套 Agent** —— 没有对应场景。
- **不照搬它的 `ToolStage`**（PRE/POST_PROCESS）—— 那是为「最终回答后自动跑」的
  聊天收尾流程设计的，本项目没有这个概念。


---

## 7. 实施与回退记录：批次 1（2026-09-16）

> ⚠️ **本节记录的是已被回退的那一版实现。** 保留它是因为里面的设计判断
> （7.2 的三处偏差、7.3 的三个发现、7.4 那个仍在的接口 bug）**与代码存不存在无关**。
> 重建时**不要照抄 7.1 的落点** —— 那一版封装错了层。

### 7.1 当时落地了什么（已回退）

```
src/requirement_agent/tools/
  __init__.py      名录（导入即注册）+ 原则声明
  base.py          ToolResult / BaseTool 契约
  registry.py      注册表（存类不存实例）
  <10 个工具各一个模块>
tests/unit/test_tool_registry.py   22 条
```

**10 个工具**：`search_requirements` / `get_requirement` / `get_features` /
`trace_requirement` / `list_relations` / `get_capabilities` / `find_streams_by_capability` /
`search_documents` / `list_pending_reviews` / `submit_requirement`。

**零调用方** —— 与 §4 的计划一致。测试 412 → **434**。

### 7.2 与 §3.1 设计的三处偏差（都是实施时定的）

1. **一个工具一个模块，不是「一工具一目录」。** CowAgent 的工具 100–500 行、各自带
   helper 与测试数据，所以值得一目录；本批是 20–40 行的转发层，一目录会变成 10 个
   几乎空的包。**契约、注册表、名录三件事完全照它的形状**，只有文件粒度按体积定。
   哪个工具长到需要 helper，再把它提成目录。
2. **用 `@register` 装饰器，不靠 `__all__` 清单扫。** CowAgent 的做法有两个代价：
   忘了加清单就**静默不注册**（它自己的注释也承认「很容易忘」）、且要靠 `cls()`
   实例化一次才能拿到 `name`（会触发 `__init__` 的副作用）。装饰器没这两个问题，
   代价是「模块必须被 import 到」—— 由 `tools/__init__.py` 显式导入 + 一条
   **断言工具集完全一致**的测试兜住。
3. **工具实例持有自己的仓储/服务实例**（在 `__init__` 里装配），不共用
   `api/dependencies.py` 的单例。理由：从 `tools/` 反向 import `api/` 是层次倒置，
   而这些依赖都是无状态的，代价只有构造开销。

### 7.3 实测抓到的三个问题（**单测发现不了，真跑 `execute` 才暴露**）

| # | 问题 | 怎么发现的 | 处置 |
|---|---|---|---|
| 1 | **`at_version=99` 静默返回当前的功能集** | 跑 `get_features(at_version=99)` → 返回 16 条（= 当前），看起来像「v99 长这样」 | **已在工具里修**：先读 `current_version` 校验范围，越界如实报错。⚠️ **接口层有同样的问题**，见 §7.4 |
| 2 | **按名字找能力时过滤 `status='active'` 等于查不到** | 跑 `find_streams_by_capability('导出')` → 「没找到」。库里 19 条能力大多 `pending_confirmation`，active 下 0 条、不限状态 4 条 | **已修**：不过滤状态，改成把 `status` 随候选一起返回，由模型自己判断要不要用一条「还没被确认」的能力 |
| 3 | （设计期就预料到、实施时确认）**多候选不能替模型挑** | 跑 `find_streams_by_capability('创建')` → 3 个候选 | 返回候选列表让它说清楚，**不猜** —— 挑错会把两条不相干的需求混起来 |

> 第 1、2 条是同一类东西：**工具对模型说了假话**（一个是假的版本状态、一个是假的「查不到」）。
> 工具的返回值会被模型当成事实来推理，所以它宁可报错也不能含糊。这条值得带进批次 2。

### 7.4 顺带在接口层发现的问题（**未修，留作待办**）

`GET /requirements/{key}/features?at_version=99`（当前版本是 V3）**同样静默返回当前的功能集**。

根因在仓储层的 SQL 判据：`origin_version_no <= N AND (removed_version_no IS NULL OR > N)`
—— 对**未来版本**，这个条件对**所有当前行**都成立。所以仓储本身是自洽的，
是**接口没有做范围校验**。

**修法**（需你定一下语义）：在 `/features` 端点里读 `current_version`，越界返回
**404**（版本不存在）还是 **409**（参数与状态冲突）？定了就是几行的事。
—— 这一条**不属于工具层**，记在这里只是因为它是在做工具时发现的，别弄丢了。
