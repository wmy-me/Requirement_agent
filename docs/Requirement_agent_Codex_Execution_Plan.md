# Requirement_agent：终端 Codex 改造执行文档

版本：1.0｜日期：2026-10-09

## 1. 执行指令与目标

请在现有仓库内按本文件分阶段实施，不要只输出建议，也不要重写整个项目。先阅读本地 AGENTS.md、当前代码、依赖与数据库迁移，再落实改动及测试。允许根据当前代码调整建议文件名，但不得改变业务边界。每阶段完成后更新实施记录，持续推进后续阶段；遇到确实缺少外部凭据时，完成适配器、模拟测试和配置说明，明确真实联调尚未完成。

目标：将网页端从“几乎所有消息都进行新需求分析”改为以需求库问答为主的助手；使用统一、可恢复的 ReAct 执行内核按需调用内部工具和 Tavily MCP；明确的需求新增/变更形成带证据的提案，最终批准后复用现有事务与版本管理执行。

ReAct 指模型根据工具返回决定下一步，采用结构化 tool calling，不解析自由文本 Thought/Action，也不向前端输出模型隐藏推理。

## 2. 依据与可信边界

参考仓库：
- https://github.com/wmy-me/Requirement_agent
- https://github.com/21breeze/NodAgent

静态阅读基线：Requirement_agent master ecb706c23c3e1610fc5ead47113db2d29c1c4d6a；NodAgent main d7853fde92930ceee866e636c055ad22821eba0e。

这些是规划时的代码快照，不是实施时的权威版本。开始前核对当前 HEAD、工作区修改及本地指令，记录差异；不要覆盖用户未提交的修改，不要强制回退到上述版本。本文件未包含真实运行验证。

已观察：真实 LangGraph 固定分析图与审核决策图；聊天 SSE 和 /agent/run 又分别手工编排；工具注册表只允许只读工具；需求检索已用关键词+向量+RRF；有长期记忆、Outbox、Worker、运行事件与模型记录基础设施。NodAgent 的 External Agent 是天气/时间/GitHub，不能当成通用网页搜索实现；其主图单轮选择一个 Specialist，不应直接照搬为跨来源调查主图。

## 3. 必须遵守的边界

1. 第一版使用一个工具调用式主 Agent，不新增独立 Memory Agent，不建设多 Agent 对话体系。
2. 保留飞书等渠道采集、审核、版本链、审计、事务和乐观锁；渠道仅提供默认模式，不能替代意图判断。
3. 问答、比较、查证不创建待审核需求；明确新增/变更才创建提案。上传附件不自动等于提交需求。
4. 正式写入、合并、删除、回退不得直接暴露为模型工具。模型只能提出变更与预览。
5. 不以自动批准代替减少人工干预；只有业务关键缺口才澄清，正式需求变更仍最终审批。
6. 不把 RRF 分数当相似度或置信度；不同 query 的候选落差不可合并计算。
7. Tavily 通过 MCP 接入；不悄悄改成直接 API。GitHub MCP 为可选扩展，不因其未配置阻断 Tavily/内部问答。
8. 不提交密钥；不执行破坏性迁移，不自动推送、合并或部署。不更换已有 Embedding 模型、维度、队列与数据库。
9. 沿用可用的模型路由、重试、计量设施；新功能不可形成另一条不可观测旁路。
10. 新增安全校验属于本次工具与运行边界，不扩张成全站身份系统重写。

## 4. 目标组件与依赖方向

API / SSE / Worker → AgentExecutionService → Graph / Agent → 统一 ToolInvoker → Application Query/Service 或 MCP Adapter。

Graph checkpointer 保存恢复状态；业务 agent_run 保存任务生命周期；事件表保存可回放事件；聊天记录保存用户/最终回复；Proposal 保存拟议变更。明确各自权威字段，不能互相用旧副本覆盖。

建议新增或调整目录（路径相对 src/requirement_agent）：

| 位置 | 职责 |
|---|---|
| application/agent_execution_service.py | 创建、认领、执行、取消、恢复与结果查询 |
| application/intent_service.py | 结构化意图判断与入口模式处理 |
| application/evidence_service.py | 保存证据、引用映射与来源校验 |
| application/proposal_service.py | 提案版本、预览、审批提交适配 |
| agents/assistant_agent.py | 单主 Agent 的工具调用策略 |
| workflows/assistant_graph.py | 工具循环、澄清、最终校验与预算分支 |
| workflows/assistant_state.py | 可序列化状态与 reducer |
| infrastructure/llm/ | 支持消息/tool_calls的异步适配，复用路由审计 |
| infrastructure/mcp/client_manager.py | 独立服务的连接、工具发现、重连和关闭 |
| infrastructure/mcp/tool_adapter.py | MCP到统一工具契约的转换 |
| config/mcp.py | 严格校验连接配置与白名单 |
| tools/requirements/ | 列表、模块、详情、版本、能力、关系、来源 |
| tools/documents/ | 文档搜索与片段读取 |
| tools/external/ | web_search/read_web_page，映射Tavily MCP |
| tools/memory/ | 查询已确认长期记忆，第一版不新增写工具 |

工具按业务能力分目录，按 Agent/consumer 分权限；不要复制共享工具。保留现有工具公开名称及 import 兼容转发，调整注册表子包加载，不允许懒加载失败后留下错误的“已加载”状态。

## 5. 阶段 0：基线检查与实施记录

- 查看 git status、当前 HEAD、AGENTS.md、pyproject.toml、迁移及现有运行方式。
- 核对 graphs.py、agents_nodes.py、agent_chat.py、agent.py、requirement_service.py、retrieval_service.py、requirement_query.py、review_service.py、tools/base.py/registry.py/invoker.py、LLM Provider/Router、run_tracking及chat仓储。
- 找到模块真实模型：模块关联在功能还是主需求上、同名模块规则、未归类功能如何处理。不能凭模块名字符串猜数据库结构。
- 检查 actor/project 范围和现有认证机制；新接口与工具必须继承并服务端验证，不信任模型传入身份。
- 运行仓库要求的基线检查，记录已有失败与外部依赖。不要把旧失败宣称为本次通过。
- 新建 docs/agent_refactor_progress.md，记录阶段、变更、测试、迁移、未完成项。

验收：产出现状调用图及入口差异；实施范围可明确定位，不先修改数据库数据。

## 6. 阶段 1：统一运行基础与模型工具协议

### 6.1 执行服务

提供语义等价于 start/get_result/subscribe/resume/cancel 的接口。接口名可适配现有框架。

- 第一阶段允许不同 graph 共用执行服务：assistant_v1 是新问答图，analysis_legacy 是旧需求图。统一生命周期不等于立即统一业务路径。
- run显式记录 task_type、execution_version、actor_id、conversation_id、source_id（可空）、原始输入、mode、联网策略、预算和运行配置版本。
- 保留 client_message_id 幂等；相同身份/会话/消息重发不创建重复任务，载荷不同返回冲突。
- 状态包含 queued/running/waiting_input/waiting_review/completed/failed/paused/cancelled，兼容已有状态及前端；审批拒绝状态按现有领域定义映射。
- 一个运行只允许一个执行者，用数据库原子认领/租约和状态条件更新，不能靠进程字典。暂停、恢复和取消都需竞态测试。
- 请求连接断开不等于取消任务。复用Outbox任务承载运行，显式cancel才停止；使用租约/心跳区分慢任务与僵尸任务。
- 有副作用的动作先保存幂等标识与提案，不宣称checkpointer提供端到端exactly-once。

### 6.2 模型接口

- 增加完整messages、tools和结构化tool_calls支持，含assistant tool_call_id与匹配的tool response；保留原generate接口供旧Skill使用。
- 同步/异步、流式工具参数增量按当前官方SDK或协议核验；不能沿用只读delta.content的解析器假装支持工具流。
- 模型调用进入既有ModelRegistry、fallback、prompt_version、run_id、token计量。主备模型必须具备所需工具能力；部分输出已发送后不可无标记拼接另一模型输出。
- 校验工具名、参数、权限；未知工具和参数错误返回可纠正错误，设重试上限，禁止无限循环。
- 同步SQL/旧Skill经受控线程池执行，HTTP/MCP优先异步；下层设置超时。超时不能被误描述成线程已终止。

验收：用假模型连续完成两个工具回合后结束；错误参数能一次纠正；没有密钥可测试并显式提示能力不可用。

## 7. 阶段 2：网页意图与需求库ReAct问答

### 7.1 输入模式和意图

模式：auto / ask_library / draft_requirement / research。网页默认ask_library或以问答为主的auto，外部专用提交接口显式draft_requirement；最终以UI现状选最小兼容改动。

意图：casual、library_query、compare_explain、external_research、new_requirement、change_requirement、operation_request。

不再以关键词“需求/接口/数据”命中决定新需求分析。用当前消息、有限会话历史、选中需求、附件上下文与模式做结构化分类。分类置信度不是执行授权；显式要求写操作必须进入提案/审批路径。auto不明确时先进行无副作用查询或一次必要澄清。

附件区分“解释此文档”和“整理此文档为需求”；接收即存档可继续，但不得自动形成正式需求。

### 7.2 工具与图

先提供list_requirements_by_module、count/list分页、get_requirement_detail、get_requirement_features、get_requirement_versions、compare_requirement_versions、list_requirement_relations、trace_requirement_sources，复用已有Query Service。

- 模块名称歧义时返回候选模块及ID，必要时澄清。
- 返回total、page/cursor、has_more、范围与排序；主需求去重，注明匹配功能数量。
- “全部有哪些”不能只返回向量Top-K。数量过多时分页展示，不能声称已完整列出。
- 授权/项目过滤进入SQL与检索前置条件；分页稳定排序，不能先全局召回再只在回答阶段过滤。
- 主图：load_context → model；model有tool_calls → tools → model；产生答案 → validate；关键业务缺口 → waiting_input；预算耗尽 → incomplete result。
- max_model_turns、max_tool_calls、deadline、token/费用预算由服务端限制。首版可配置默认8轮、12次调用、120秒，外部延迟/场景测试后调整；没有准确费用信息时不要声称有精确费用硬限制。
- 校验失败最多有限次数修订，无证据不能编造需求编号。

验收：“登录模块有哪些需求”只查询；“两个版本改了什么”读取版本；“新增手机号登录并整理提案”进入提案路径；普通闲聊正常回答，不再固定介绍文案。

## 8. 阶段 3：Tavily MCP与外部证据

实施前核验Tavily官方MCP当前transport、地址、认证、工具清单、依赖版本。不要照抄聊天中示例工具名或把URL/密钥猜成事实。远程HTTP优先；若必须stdio，明确子进程与退出处理。记录采用版本及官方文档链接，不向日志打印含密钥URL。

配置至少含enabled、transport、endpoint、credential_env、allowed_tools、timeout、result_limit；运行配置off/auto/on。提供.env.example占位与缺失凭据提示。独立加载各server，GitHub未配置不应影响Tavily。

- 公开工具名web_search/read_web_page，底层显式映射发现的Tavily搜索/提取能力；不开放所有发现的MCP工具。
- 内部/MCP调用统一schema、consumer/actor权限、run_id、tool_call_id、计时与失败记录。
- 外部检索使用脱敏关键词，不自动发送内部全文、身份信息或长期记忆。用户提供URL也做scheme与公网目的地址检查；阻止私网/loopback/link-local、危险协议与重定向绕过。远程供应商代理请求时也限制应用能请求的目的地，并记录供应商侧边界。
- 区分网页读取成功、仅摘要、失败、内容截断；保存URL、标题、时间、支持结论的片段和内容hash。
- MCP结果/网页正文作为不可信资料，不允许其中“忽略规则/执行删除”等文本获得工具权限。
- 429/超时有限重试，不把工具失败描述成未找到；web=off硬禁用外部调用；web=on仍遵守预算和敏感信息边界。

验收：离线模拟搜索/提取、断线、限流、异常参数、未配凭据、恶意网页指令；有用户本地凭据后可选择真实联调，未联调则如实记录。

## 9. 阶段 4：统一证据、文档检索与记忆

Evidence建议字段：evidence_id、run_id、query_id、source_type、source_ref、title、version/locator、excerpt、retrieved_at、content_hash、retrieval_health。需求定位到REQ与版本/功能；文档定位到document/chunk/page；网页定位到URL和已获取内容。

- 多轮工具结果使用稳定ID，显示编号由服务端映射，不每次检索都从[1]覆盖旧来源。
- 最终模型仅引用已授权且本次已取回的证据；拒绝不存在编号、无访问权限及目标版本错配。
- 引用存在性硬校验；重要结论做支持关系检查，允许标“推断/建议”，不要把模型二次判断宣称为事实证明。
- 回答正文与结构化sources一起保存；流式草稿允许未校验，但最终结果标识必须明确。前端支持点开来源和定位。
- 保留需求RRF；新增文档Chunk检索并核验原文件页码映射。中文关键词分词需数据集验证，不能默认PostgreSQL英文FTS适合中文。
- 检索返回vector_available、keyword_available、degraded_reason、filters、query_id。Embedding失败时明确部分降级；“没查到”不等于“确认不存在”。
- 复用长期记忆，调查前加载已确认偏好/项目约束；当前消息优先；未审批提案不能成为正式长期事实。第一版不自动写记忆，不新增Memory Agent。

验收：多轮内部+外部引用不冲突；虚构引用被拒绝；文档证据可定位；跨项目不可读；Embedding失败仍能问答但显示检索缺口，不输出确定的独立需求结论。

## 10. 阶段 5：持久化、恢复与SSE

- 选择与当前LangGraph版本兼容的持久化checkpointer（如Postgres），依赖和setup方式需核验。checkpoint与业务迁移分开管理，迁移追加编号。
- conversation_id是业务对话，run_id是本轮任务，checkpoint thread标识按执行策略绑定并保存。不要直接将用户提供thread_id当跨用户访问键。
- 状态存完整输入/模式、消息与tool_calls/results、证据ID、预算、待澄清问题、proposal_id；不存session/repository/client等不可序列化对象。
- 保存工具结果并建立调用幂等关联；崩溃窗口可能导致只读查询重执行，需说明语义并在预算计量中处理；正式变更通过事务幂等避免重复。
- interrupt澄清/审批与手动pause区分；恢复先校验actor、状态、输入对应的interrupt与配置版本，原子认领。避免旧协程覆盖cancelled/paused。
- SSE为持久化事件的传输视图：run_started、tool_started/completed、evidence_added、waiting_input、proposal_ready、waiting_review、completed/failed/cancelled；为旧step/narrative/artifacts/done提供兼容映射或同时更新前端与契约。
- 只有最终用户回答输出token，不泄漏隐藏推理或内部工具参数。持久化事件有sequence并支持after_seq；token可不逐条存，最终正文必须可回放。
- 修改invocations接口读取真实模型记录，修复失败前序事件丢失；业务状态维护失败应失败或重试，非关键观测失败可降级记录，不能把所有写库都当“可忽略追踪”。

验收：重启恢复；SSE断线不重算；同消息重试幂等；两个resume只有一个成功；取消后不能复活；其他用户不能恢复/读取该run。

## 11. 阶段 6：需求提案与最终审批，迁移旧分析

新增RequirementProposal，最少包含：id/version、source/run/actor、任务类型、title/summary、结构化功能与验收条件、目标主线及基础版本、拟议diff、重复/关联/冲突证据、风险、假设/缺口、引用、完整性状态、content_hash和审批状态。

- new/change意图调用现有抽取、风险与内部工具，按需调查。抽取和风险仍为确定输入输出能力，不必独立ReAct Agent。
- 修复RiskAssessment字段为low/medium/high枚举，归一化已知值，不认识的值标不完整/降级；禁止无效值绕过高风险判断。
- 不再用“未命中重复/高风险”直接表示可自动落库；提案满足schema、证据和必要字段才能ready_for_review，降级/缺口明确展示。业务允许例外时审批人显式确认。
- 正式需求变更一律最终审批，Agent能自行修草案；查询结果不审批。澄清问一次关键问题集，不能每个工具回合打断用户。
- 审批绑定proposal_id/version/hash、target requirement/current_version、reviewer及权限；审批拒绝/退回/批准映射现有ReviewService，不让模型直接执行。
- 批准执行前检查版本与内容是否变化，变化则重新预览/审批；用事务、乐观锁和幂等key防重复落库。审批后Agent不能修改已批准执行载荷。
- 迁移后台需求分析、/agent/run和聊天提案入口到同一新编排；若旧响应字段不同，提供显式adapter。未完成迁移不得声称“三套流程已统一”。
- 自主重复/关系建议保留查询证据，但不得将余弦高直接当业务重复；通过对象、功能、约束核对，最终主线归属人工裁决。

验收：完整提案→批准→唯一版本变更；退回可修改并形成新提案版本；拒绝不写正式需求；重复批准不重复执行；旧目标版本审批被拦截；飞书/文件采集回归通过。

## 12. 暂缓与后续部署

本次核心阶段不强制多Specialist、Redis/Celery迁移、向量库替换、Embedding更换、自动记忆写入、GitHub写操作、容器上线。新执行器优先复用现有Outbox/Worker。

文档解析若聊天路径仍同步，应移交现有Worker，统一queued/parsing/chunking/embedding/ready/failed状态；处理未完成明确等待，禁止截取前6000字却不说明证据缺失。按document checksum、解析/切块/embedding版本做幂等，不与Agent任务共享不可控长事务。

核心验收后可新增Compose：API、Agent Worker、Document Worker、PostgreSQL/pgvector、前端/Nginx；MCP视选用远程服务无需本地容器。数据库迁移单独执行，提供健康检查、共享对象存储/持久卷和secret环境变量。不自动down -v或发布。

## 13. 必须执行的验证矩阵

| 场景 | 预期 |
|---|---|
| 模块全部需求，多页/重名/未归类 | 结构化过滤与total正确，必要澄清，无假完整列表 |
| 普通闲聊、解释附件、飞书查询 | 不误创建需求 |
| 新需求、已有需求修改 | 创建草案/提案，未批准不写正式版本 |
| 内部+Tavily多轮查询 | 同一run，稳定证据与工具记录 |
| Embedding/MCP/模型失败 | 可见错误或部分降级，无伪造确定结果 |
| 工具参数错误、未知工具、预算耗尽 | 有限纠正，明确不完整，循环结束 |
| 恶意网页要求执行删除 | 不执行、不提升权限 |
| web关闭/未配Tavily | 不联网，内部查询继续可用 |
| 跨用户/项目run、证据、审批 | 服务端拒绝访问 |
| 断线、进程崩溃、双恢复、暂停取消竞态 | 可恢复或明确终态，无重复正式动作 |
| 风险中文/大写/非法值 | 归一或失败标记，不绕过检查 |
| 提案变化、目标版本变化、重复批准 | 重新审批或幂等，无错误版本覆盖 |

使用确定性假模型和假MCP验证核心协议与状态机；集成测试用隔离数据库，不能指向用户生产库。针对新增逻辑和关键边界增加测试，不大量编写只镜像实现的测试。执行仓库既有相关单元/集成/类型/格式检查；命令以当前配置为准，缺外部依赖要记录未运行而非通过。

## 14. 交付与完成判定

每阶段应留下可运行代码、必要追加迁移、配置示例、相关测试、兼容说明和进度记录。未接完的功能不得显示“执行成功”，不要仅创建空壳文件。

最后提交实施报告，列明：
- 已完成阶段与具体行为变化；
- 仍保留旧入口/旧图的原因及删除计划；
- 测试命令、结果、旧失败与未运行检查；
- 新环境变量、迁移顺序、启动/回滚方法；
- Tavily真实联调是否完成、采用的官方工具映射；
- 预算限制、恢复语义、引用校验的实际能力与限制。

保留可配置legacy/new执行版本开关用于比较和回滚；旧run按原execution_version恢复，不能强行用新状态格式读取。迁移数据库优先追加且可向后兼容；不要删除旧数据来完成改造。

## 15. 用户可复制给终端Codex的启动指令

请阅读并执行本文件。先核对AGENTS.md、当前HEAD和工作区修改，记录与静态基线的差异，然后按阶段0至6实施。优先形成可运行的网页需求库ReAct问答，再接Tavily MCP、引用与恢复，最后迁移需求提案和统一三套编排。复用现有审核、版本、Outbox、检索和模型审计；不新增多Agent体系，不直接开放正式写工具。每阶段运行相应验证并更新docs/agent_refactor_progress.md。凭据缺失时完成可测试适配与说明，不编造真实联调成功。不要自动推送、合并或部署。
