# Requirement Agent 重构实施记录

版本：1.0｜日期：2026-10-09

## 交付完整性修复（基线 `5bca42a`）

- 验收发现：`5bca42a` 已引用动态路由、助手、MCP、checkpoint 等新增模块，但这些实现及其测试仍停留在工作区，造成全新 checkout 的导入闭包不完整。
- 本修复将实际实现、覆盖它们的测试、状态迁移、验证脚本和相关文档一并纳入版本控制；不包含 `.env`、凭据、上传件、缓存或数据库导出。
- 已执行 `git status --short`、`git ls-files --others --exclude-standard`、`.gitignore` 审查和 `git diff --cached --check`。修复前没有其他可入库的未跟踪文件；隔离 checkout 的安装、导入和完整测试结果将在本次交付验证后据实补充。
- 隔离验证在全新本地 clone 完成：`uv sync --frozen --all-groups` 成功；应用与新增模块导入成功；对专用空 pgvector 数据库顺序执行 `001`–`027` 迁移成功（28 张 public 表）。运行时显式设置专用测试库、专用测试角色、测试 API token，并禁用 Tavily，未读取 `.env` 或连接默认数据库。
- 全量命令 `pytest -q` 的结果为 **687 passed, 24 skipped, 33 failed**。33 个失败均为既有用例假设预置业务样本（例如 `REQ-000015`）或真实 embedding/模型凭据；空数据库没有这些种子数据，且本验证不使用真实凭据。未将其伪装为通过，也未向隔离库导入未知来源数据。
- 本次补齐模块的定向验证命令 `pytest -q tests/integration/test_dynamic_chat_checkpoint.py tests/unit/test_assistant_agent.py tests/unit/test_dynamic_requirement_graph.py tests/unit/test_list_requirements_by_module.py tests/unit/test_tavily_mcp.py`：**19 passed**。后续应补充可版本化的脱敏测试种子和离线 embedding fixture，使完整套件可在全新环境中全绿。

## 0. 基线检查

- 当前 HEAD：`b04fde9`（执行计划中的静态基线为 `ecb706c`）。
- 工作区已存在若干局部升级：`agent.py`、`agent_chat.py`、`workflow`、`agent_run`、`chat` 仓储、`tests/unit/test_agent_chat_narrative.py`、`tests/integration/test_conversation_requirement_drafts.py`、`tests/integration/test_run_resume.py` 等。
- 根目录未发现 `AGENTS.md`；因此按文档要求记录为：未提供本地 AGENTS 说明文件，无法按文件内规则执行额外约束，当前按仓库代码与本执行计划执行。
- 基线检查执行：`git status --short` + `git rev-parse --short HEAD` + `pytest -q`。
- 结果：当前仓库在执行前基线已通过，724 个测试全部通过；本次改造不宣称回退历史，也不覆盖已提交/未提交用户修改。

## 1. 阶段 0：基线检查与实施记录

已完成：
- 核对了 git 状态、当前 HEAD 与现有工作区差异。
- 核对了 `pyproject.toml`、迁移目录、`src/requirement_agent` 中的核心模块与工作流。
- 确认当前仓库以 Python 3.12 + src-layout 的方式组织代码，保留了现有 `workflow/graph`、`run_tracking`、`review_service` 与 `tool` 体系。
- 运行了基线测试，记录 `724 passed`，并保留旧失败/外部依赖说明在本记录中。

## 2. 阶段 1：统一运行基础与模型工具协议

已完成：
- 扩展 `agent_run` 状态集，新增 `waiting_clarification`，并让它纳入非终态/活动状态语义，保证暂停与继续逻辑兼容现有状态模型。
- 运行图保留纯计算语义，保证 `run_analysis` 不是直接写库入口；`prepare_review` 与 `router` 只负责路由、状态和审计，不执行正式写入。
- `agent_chat.py` 中新增 resume 与 checkpoint 恢复逻辑，能够在澄清中断后继续执行。
- `chat_repo.find_clarification_run()` 用于定位等待补充的会话 run，并恢复其 checkpoint。
- `LLMProvider.complete_with_tools()` 增加 OpenAI-compatible `messages`、`tools`、`tool_calls` 与
  `tool_call_id` 响应协议；旧的 `generate`/流式接口保持兼容。
- 新的单助手循环限制为最多 8 个模型回合、12 次工具调用和 120 秒服务端时限。
  参数非法、未知工具和权限不足均以可纠正的工具错误返回，不解析自由文本 Thought/Action。

实际行为：
- 普通聊天不会再被固定为一段模板语句，而是调用配置好的问答模型生成回答；缺配置时明确返回降级提示。
- 要求补充信息时，不直接创建正式需求，也不擅自执行落库动作。

## 3. 阶段 2：网页意图与需求库 ReAct 问答

已完成：
- 新增 `workflows/router.py`，实现 `general_question` / `requirement` / `unclear` 路由与安全降级。
- `router_node` 支持模型输出校验、前置条件检查、非法动作回退；超出次数或超时限定后走 `route_limit` 失败模式。
- `run_analysis` 支持 `conversation_mode` 和 `intent` 参数，并在 `state` 中保留 `route_action`、`route_reason`、`route_history` 等字段。
- `extract/retrieve/analyze/risk` 节点依次产出 `completed_steps`，保证图允许在审计事件里清晰呈现执行路径。
- 增加 `IntentService`：支持 `auto`、`ask_library`、`draft_requirement`、`research` 模式；
  auto 默认走无副作用的需求库问答，只有显式新增/变更/草稿模式才进入既有需求分析路径。
- 增加 `RequirementAssistantAgent`：以结构化工具调用循环使用既有只读需求工具，生成稳定的
  tool-call/evidence artifacts 并写入对话 run checkpoint；普通查询不创建待审核需求。
- 新增 `list_requirements_by_module`，按真实的 `requirement_feature.module_key/module_name`
  关系分页查询主需求，返回 `total/page/limit/has_more` 和匹配功能数，不从标题猜模块。

行为说明：
- 普通闲聊走 `answer` 路径，不产生需求工件或正式需求分析。
- 需求材料在明显缺字段时走 `clarify` 路径；只有补全后才能继续检索/分析。
- `prepare_review` 作为前置审查节点，避免模型绕过多步分析直接宣称已完成。

## 4. 阶段 3：Tavily MCP 与外部证据

已完成（离线可测，未做真实外部联调）：
- 新增 Tavily MCP 专用配置与远程 streamable-HTTP 客户端；只接受官方 `https://mcp.tavily.com/mcp/`、`http` transport、`TAVILY_API_KEY` 环境凭据及 `tavily-search`/`tavily-extract` 白名单。
- 新增只读 `web_search` 与 `read_web_page` 工具，显式映射上述 MCP 工具；不调用 Tavily REST API，也不公开其他发现的 MCP 工具。
- 缺少凭据、禁用、不可用或未发现允许工具时返回明确错误。网页 URL 仅接受可解析到公网地址的 HTTP(S) 目标，并校验供应商返回的最终 URL。
- 增加假 MCP transport 的离线测试，覆盖禁用、缺凭据、工具映射、结果归一化与私网 URL 拒绝；未使用真实 API key 或进行网络联调。

## 5. 阶段 4：统一证据、文档检索与记忆

已完成/保留：
- `RequirementService` 在 metadata 中保存 `routing` 字段，用于记录意图、路由历史和错误状态。
- `run_tracking` 仍保留对工具调用、事件和状态的记录，不在新图中绕过审计记录。
- `agent_chat` 中 checkpoint 版本化恢复保留了 `invalidation_history` 等状态，用于在补充信息变更时清理旧证据。
- ReAct 工具结果会构造本次 run 内稳定的 `E1...` evidence 记录（来源类型、引用、摘要、内容 hash），
  并随最终 assistant artifacts 和 checkpoint 持久化；工具事件进入既有 run event/tool invocation 记录。

限制：
- 第一版不新增 Memory Agent，也不写入自动长期记忆。
- 证据引用仍以现有检索/工具调用记录为主，未构建多 Agent 记忆图。

## 6. 阶段 5：持久化、恢复与 SSE

已完成：
- 增加 checkpoint 与 run-state 恢复逻辑，覆盖补充信息后继续执行的场景。
- `agent_chat` 兼容旧 run 流和新动态路由流；断线/重试时可以继续使用保存的状态与上下文。
- 运行事件、状态和补充信息的 invalidation 逻辑已在测试中被覆盖。
- 新 assistant run 同样保存 execution version、意图、联网策略、工具调用和 evidence；预算耗尽会返回
  明确的 `incomplete` 结果，不伪装成成功完成。

## 7. 阶段 6：需求提案与最终审批

当前状态：
- 本仓库仍保留 `ReviewService`、需求版本和需求审核链，不将模型直接写入正式需求库。
- 新图中 `prepare_review` 只产出“可提交审核”/“需人工审核”决策，未绕过审批流程。
- 代码实现保持“纯计算/提案准备”与“真实写入审批”分离，符合文档要求。

## 8. 验证矩阵与结果

已执行：
- `pytest -q`

结果：
- `724 passed`，耗时约 45.78s

已覆盖的关键场景：
- 普通问答不走需求分析；
- 缺字段时进入澄清流程；
- 补充信息后旧证据失效且恢复从 checkpoint 继续；
- 运行状态与事件流兼容恢复/取消语义；
- 需求审查/最终决策仍保留人工审批路径。

## 9. 环境变量、迁移顺序与启动方式

- 现阶段未新增破坏性数据库迁移；保留现有 `migrations/` 顺序，新增改造以追加迁移为主，不删除旧数据。
- 现有数据库关联主要在 PostgreSQL + pgvector；任何新迁移都按增量方式追加，确保旧 run/status/审计/需求版本链继续可读。
- Tavily MCP 层已完成离线适配但未进行真实联调；上线时需设置 `TAVILY_MCP_ENABLED=true` 和 `TAVILY_API_KEY`，保留固定官方 endpoint、允许工具、超时及结果上限配置。
- 本地 `.env` 已补齐缺失的非敏感运行键与禁用状态的 Tavily MCP 配置；空 JSON 配置使用 `{}`，避免
  Pydantic 在启动时因空字符串解析失败。真实密钥没有写入或输出。
- 启动方式仍沿用仓库现有 Python 方式：以 `.venv/bin/pytest -q` 作为回归验证，运行小说明里保留 `uvicorn`/FastAPI 入口与 `main.py` 方式；未执行自动推送/部署/合并。
- 回滚方式：保留旧图/旧入口并保留原 `execution_version` 状态，不强制迁移旧数据，必要时直接回退到旧 graph 或关闭新路由配置。

## 10. 现状说明与后续清单

已保留：
- 旧图/旧入口与新图并存；通过保留 `execution_version` / 状态隔离的方式减少回滚风险。
- 需求审核、版本管理、Outbox 与 run tracking 等关键机制不被替换为另一路不可观测系统。

待继续处理（真实需求边界内）：
- 真实 Tavily MCP 联调配置与官方工具映射；
- 真实 Tavily 联调及线上认证；当前仅有假的 MCP transport 覆盖，未使用用户密钥。
- 提案独立持久化模型、版本/hash 审批绑定，以及将聊天草稿完全迁移至该提案模型；
- LangGraph PostgreSQL checkpointer 与原子租约认领；当前仍以既有 agent_run/checkpoint 机制恢复。

## 11. 结论

本次改造按文档重点落地了：
- 更安全的动态路由图；
- 需求库问答优先的、有界结构化 ReAct 工具循环；
- Tavily MCP 只读适配与外部 URL 安全边界；
- 模块真实关系的稳定分页查询；
- 普通问答与需求分析分离；
- 依据补充信息的 checkpoint 恢复；
- 非正式写入的审计状态与路由记录；
- 可回滚的统一运行基础，不破坏现有需求审核与版本管理链。

未完成的真实外部联调部分已如实记录；未编造成功结果，也未伪装为已部署/已开放生产能力。

## 12. 本轮可靠性、工具与旧 Agent 迁移（进行中）

已完成：
- 助手循环在每次模型调用前后、每次工具调用前检查 `paused/cancelled`；最终状态仅在数据库仍为 `running` 时条件更新，避免旧协程覆盖暂停或取消。
- 工具开始/结束事件在每次调用时立即写入运行事件，工具调用明细独立保存；模型/工具预算与总 deadline 产生明确 `incomplete` 结果。
- Tavily MCP 的 `isError`、文本 JSON 解析失败和非对象 payload 都返回 `ToolResult.error`，不再伪装为 empty。
- auto 意图中“查看新增需求有哪些”“删除功能有哪些需求”为查询；“删除 REQ-1”为受控操作请求。
- 助手只使用同会话最近 8 条 user/assistant 历史，忽略 artifacts/tool payload。
- 新增 `tools/requirements` 中的 `extract_requirement`、`analyze_requirement_relation`、`assess_requirement_risk`，包装保留的 ExtractAgent、AnalyzeAgent、RiskAgent；分析图经统一 invoker 调用。关系分析继续保留现有证据/双闸门规则，风险结果包含降级信息。
- 新增注册表驱动的 `docs/agent_tool_catalog.md` 及生成脚本；审批、落库、合并、删除、回退仍被注册表拒绝，消费者权限由 invoker 执行。

仍未完成：
- 三个入口尚未完全收敛为单一 `AgentExecutionService`：聊天助手已加强，旧 `/agent/run` 与 Worker 仍有兼容编排。
- documents/memory 子包还没有已注册业务工具；旧需求查询工具继续保留顶层兼容模块，尚未物理移动。
- 尚未实现独立 Proposal 持久化、hash/基础版本绑定审批；既有 ReviewService 流程未替换。

本轮验证：
- `.venv/bin/python -m pytest -q tests/unit/test_assistant_agent.py tests/unit/test_tavily_mcp.py`：15 passed。
- `.venv/bin/python -m pytest -q tests/unit/test_assistant_agent.py tests/unit/test_tavily_mcp.py tests/unit/test_risk_skill_robustness.py tests/unit/test_extract_agent.py tests/unit/test_analyze_skill_consistency.py`：27 passed。
- `.venv/bin/python -m py_compile ...`（本轮修改的助手、MCP、工具包装、工作流与目录生成脚本）：通过；`git diff --check`：通过。
- `tests/integration/test_workflow_tool_wiring.py` 在本机数据库连接池已失效时失败（`psycopg.OperationalError: connection is bad`）；这不是通过结果，待使用隔离可用数据库重跑。

## 13. 第二版：统一执行与可恢复调查（部分完成）

已完成：
- 交付完整性修复已提交为 `6afa4d3`：遗漏的三个结构化分析工具、注册表生成的工具目录、生成脚本及隔离接线测试均已进入 Git；未包含 `.env`、凭据或业务数据。
- 新增 `AgentExecutionService` 的最小生命周期内核，以及 `agent_run` 原子 `claim_run`、仅当 `running` 时才可终结的 `finish_if_running`。`/api/v1/agent/run` 已切换到该服务，保存事件、工具调用和 v2 checkpoint；双认领只有一个成功，取消后迟到完成不会覆盖终态。
- 新增 `028_agent_run_execution_lease.sql`，仅添加可恢复状态索引，无破坏性迁移。
- 专用空库 `requirement_agent_codex_20261010` 已完整执行迁移，新增工具包装及分析图节点接线测试在该库、无真实模型/业务样本条件下通过。

部分完成：
- 聊天助手仍使用 ChatRepository 的兼容生命周期；其工具事件已即时落库，但 SSE 仍未改为跨线程实时转发。Worker/RequirementService 仍经 RunTracking 兼容路径，尚未改用 AgentExecutionService。
- assistant checkpoint 现保存工具调用和 sources，但未逐回合保存完整模型消息、工具原始返回、预算快照，尚不能保证进程中断后按保存结果续跑且不重置预算。

未完成：
- 三个入口统一到 AgentExecutionService；SSE 实时订阅/断线后后台继续；跨入口恢复和租约过期处理。
- 证据 ID 完整闭环（把服务端 ID 注入模型 tool result、版本/文档/web locator 完整持久化）及独立的引用定位模型。
- documents/memory 实际工具、Proposal 持久化与审批绑定（本版明确不扩展）。

第二版当前验证：
- 在专用迁移库运行 `DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:5432/requirement_agent_codex_20261010 API_AUTH_TOKEN=test-api-token TAVILY_MCP_ENABLED=false .venv/bin/python -m pytest -q tests/integration/test_analysis_tool_wiring_isolated.py`：3 passed（注册、实际执行、工作流接线、原子双认领/条件终态）。

## 14–20. 第二版续作：执行所有权与恢复边界（进行中）

已完成（待专用库认证恢复后重跑集成验证）：
- 新增 `029_agent_run_execution_ownership.sql`：`execution_token`、`lease_expires_at` 及 running 租约索引，保持旧 run 可读。
- `claim_run` 以单条条件 UPDATE 生成新的 UUID token；queued/paused/retrying 或已过期租约的 running run 才能被认领。
- heartbeat、checkpoint、complete 与 fail 都以 `(run_id, status='running', execution_token)` 为写入栅栏。旧执行者在暂停/取消、过期重认领后不能写 checkpoint 或终结 run。
- `/agent/run` 已传递认领 token 至 checkpoint/终结调用。

部分完成：
- 已新增 A 过期、B 重新认领、A 迟到 checkpoint/complete 被拒绝的隔离测试；当前本地 PostgreSQL TCP 认证拒绝空密码，未能在本轮重跑，不能计为通过。

未完成：
- 聊天和 Worker 接入 token/租约执行服务；实时 SSE 订阅、后台任务解耦、逐回合 ReAct 完整恢复状态、稳定 evidence locator 与恢复后引用闭环。

## 15. 所有权写入审计（进行中）

已完成：
- `/agent/run` 的 started、普通运行事件、工具调用、checkpoint、completed/failed 已改经 `AgentExecutionService` 的 token 写入接口；旧 token 不能继续追加事件、工具调用或 checkpoint。
- completed/failed 状态和对应最终事件改为同一数据库事务；仅成功取得 token 所有权时返回成功。

未完成：
- ChatRepository/聊天流、RunTracking/Worker 仍有无 token 写路径，尚未迁移；因此三个入口所有权闭环和实时 heartbeat/异步后台执行尚未完成。
- 追加的 token 写入路径仅完成静态编译检查；隔离库认证仍阻断该轮真实竞态测试，不能计为通过。

## 16. 工具契约、审计与分类（进行中）

已完成：
- 三个结构化分析工具的 output schema 改为复用 `ExtractedRequirement`、`AnalysisResult`、`RiskAssessment`；执行后再次校验输出，非法形状会被 BaseTool 转为 error。
- 三个模型驱动分析工具显式使用 60 秒等待上限，不再继承 5 秒查询默认值；线程超时仍只表示调用方停止等待，不声称取消底层模型任务。
- 修复 BaseTool 分页审计计数：优先读取 `total/count`，不再把分页 envelope 的字段数记作结果数。
- 工具调用持久化参数改存脱敏/截断摘要，嵌套文本不会原样进入审计；敏感键被屏蔽。
- 注册表契约测试已将三个 workflow-only 分析工具列为真实工具，并明确它们不开放给 assistant consumer。

未完成：
- 需求查询/检索/预演模块尚未物理迁移至 `tools/requirements`（需要分批移动和兼容转发，未在本轮仓促完成）；external Tavily 未改变，documents/memory 未创建空目录。
- 任务剩余预算尚未下传至 BaseTool，分析工具的模型/数据库下层超时及有界线程池仍待结合执行器改造。

## 17. 本轮正确性修复（部分完成）

已完成：
- 删除无意义 `ExtractRequirementOutput` 子类；抽取工具直接返回 `ExtractedRequirement`，无 LLM 时经 invoker 验证为 `success` 且保留 `extraction_source=heuristic`。
- 工具调用记录的 token 校验、行锁和 INSERT 合并到同一事务；空工具列表通过 heartbeat 单独确认所有权，不再把空结果当作失权。
- `/agent/run` 现在检查 checkpoint 与 complete 返回值，失去所有权会失败而非返回 `status=ok`。
- 审计摘要改为公共 `audit_summary`，递归屏蔽敏感键，限制层级、条数及最终长度；invoker 不再导入私有函数。

实际验证：
- `.venv/bin/python -m pytest -q tests/unit/test_tool_contract.py`：65 passed（含 heuristic 抽取与嵌套 api_key 脱敏）。
- `py_compile` 与 `git diff --check`：通过。

未验证：
- 隔离 PostgreSQL 的 A/B 接管、heartbeat、迟到工具 INSERT 和最终状态竞态仍受本地 TCP 数据库认证阻断，未将其报告为通过。
- 实际周期 heartbeat、任务剩余时间向工具传播、异步后台化与聊天/Worker 迁移尚未完成。
