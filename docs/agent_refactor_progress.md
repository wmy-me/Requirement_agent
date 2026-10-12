# Requirement Agent 重构实施记录

版本：1.0｜日期：2026-10-09

## 隔离外部依赖复测（2026-10-12）

- 专用 PostgreSQL 验收库已覆盖 API、pgvector 与模型调用观测的运行时配置。真实 Embedding
  使用合成文本返回 `Doubao-embedding` 4096 维有限向量，写入/查询/来源记录均通过并清理。
- DeepSeek 最小真实调用仍返回 401；Tavily MCP 保持禁用且未提供凭据，二者准确标为配置阻塞，
  未替换模型、未绕过 MCP 或把本地假服务当真实通过。完整命令和未完成矩阵见
  `docs/full_function_test_report.md`。

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
- 新增 Tavily MCP 专用配置与远程 streamable-HTTP 客户端；只接受官方 `https://mcp.tavily.com/mcp/`、`http` transport、`TAVILY_API_KEY` 环境凭据及 `tavily_search`/`tavily_extract` 白名单（兼容旧连字符配置）。
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
- 对话 API 的 `web_policy` 默认值与前端统一为 `auto`：只有外部调研意图才允许 `web_search` /
  `read_web_page`，显式 `on` 仍可强制开放；Tavily 未启用或缺凭据时，工具调用保持 error 记录，
  不把外部失败伪装成无搜索结果。

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

## 18. 隔离数据库所有权验证（部分完成）

已完成：
- 建立独立 `requirement_agent_codex_isolated` 测试库及专用非 superuser 测试角色；完整执行 `migrations/*.sql`，扩展/迁移由容器内管理员执行，测试由专用角色执行，未连接业务库。
- `tests/integration/test_analysis_tool_wiring_isolated.py` 使用线程 Barrier 覆盖两个执行者同时认领；只有一个获得 token。
- 真实数据库覆盖取消后的迟到 complete 拒绝、租约过期接管后的旧 token checkpoint/event/tool invocation/complete 拒绝，及新 token checkpoint 成功。

实际验证：
- 专用临时凭据环境下运行 `.venv/bin/python -m pytest -q tests/integration/test_analysis_tool_wiring_isolated.py`：**5 passed**。

未完成：
- 周期性 heartbeat 尚未接入实际模型/工具循环；当前仅有可调用的 heartbeat 原语。
- `/agent/run` 仍在 async 路由同步执行 `run_analysis`，取消/暂停不能在长模型调用期间及时生效；任务剩余时间未下传工具层。本轮不将其标记为完成。

## 19. `/agent/run` 实际续租、任务预算与非阻塞执行（部分完成）

已完成：
- `AgentExecutionService` 为 `/agent/run` 提供可配置的租约和周期 heartbeat（`AGENT_EXECUTION_LEASE_SECONDS`、`AGENT_EXECUTION_HEARTBEAT_SECONDS`）。heartbeat 间隔在运行时钳制为小于租约；完成、失败、预算耗尽、暂停/取消或失权后都停止续租。
- 续租规则明确为：租约即使刚过期，只要尚未被另一执行者通过原子 claim 接管，持有原 token 的 running 执行者仍可续租；一旦被接管、暂停或取消，heartbeat 条件更新失败，旧执行者不再启动后续模型/工具边界调用。
- `events(run_id, token, [])` 现在仍在带行锁的 token/状态检查下执行；空批次在仍拥有执行权时成功，失权时失败。空工具批次也使用明确所有权探测，未再把空列表解释为失权。
- `/agent/run` 将同步 `run_analysis` 放入固定大小线程池，HTTP 事件循环不再被该同步分析占用；run id 的 context 与 execution guard 一并传播。该端点尚保持原有“请求等待结果”的响应形状，未在本批次改成脱离连接的后台任务。
- 分析图的 router、extract/retrieve/analyze/risk 在模型/工具调用前后检查执行权和 wall-clock deadline。已进入的 Python 同步调用不能被强杀；底层 HTTP 通过 `LLMProvider` 取不大于任务剩余时间的 timeout，工具实际等待时间取工具上限和剩余预算的较小值。
- `/agent/run` 在 run metadata 与 checkpoint 持久化 `deadline_at_epoch`、剩余预算和执行位置；预算耗尽写入 `run_incomplete` 最终事件并返回 API `status=incomplete`，不返回普通成功。数据库现有状态集没有 `incomplete`，持久状态为 `failed` 并带明确原因，避免伪造完成。
- `BaseTool` 改为固定大小执行池。超时只表示调用方停止等待，不能表示下层 HTTP/数据库/模型调用已被取消；固定容量防止连续超时无限创建后台线程。

实际验证：
- `.venv/bin/python -m pytest -q tests/unit/test_tool_task_budget.py tests/unit/test_extract_agent.py tests/unit/test_analyze_skill_consistency.py`：**12 passed**。覆盖剩余预算小于工具上限和预算已耗尽时不执行工具。
- 在独立库 `requirement_agent_codex_isolated`、专用非 superuser 测试角色、无真实模型与业务样本下运行 `tests/integration/test_analysis_tool_wiring_isolated.py`：**9 passed**。覆盖真实数据库 Barrier 双认领、取消与迟到 complete、租约过期接管后旧 token 拒写、空事件语义、长于初始租约的 heartbeat 续租、取消后 heartbeat 失权停止、暂停后重新认领。
- 最终组合命令 `.venv/bin/python -m pytest -q tests/integration/test_agent_run_tracking.py tests/integration/test_analysis_tool_wiring_isolated.py tests/unit/test_tool_task_budget.py tests/unit/test_extract_agent.py tests/unit/test_analyze_skill_consistency.py`：**35 passed**。工作流追踪夹具已更新为当前四个分析工具的真实注册调用链。
- `.venv/bin/python -m pytest -q tests/unit/test_agent_run_nonblocking.py`：**1 passed**。用同步阻塞假分析器验证 `/agent/run` 将工作投至有界执行池，async 事件循环仍可继续调度；暂停/取消的真实 token 失权行为由上述隔离 PostgreSQL 测试覆盖。
- 本轮修改模块及测试已通过 `python -m py_compile`；`git diff --check` 通过。

仍未完成：
- `/agent/run` 目前只在图节点边界合作式停止，尚未逐回合持久化模型消息/工具原始返回或从 checkpoint 续跑；因此不能宣称中途进程崩溃后的 exactly-once 或完整 ReAct 恢复。
- 仍未把聊天和 Worker 迁移至该执行服务；聊天/Worker 的旧入口仍保留兼容编排。
- 未实现后台任务与 SSE 解耦、断线回放或实时事件订阅；这些属于后续批次。
- 未对真实外部 LLM、HTTP、Tavily 或生产数据库做联调。本批验证使用假模型/离线工具和隔离 PostgreSQL。

## 20. 执行边界补丁（部分完成）

已完成：
- 分析与工具执行池均增加固定“运行中 + 排队中”准入上限；容量等待受任务剩余时间限制。容量或预算耗尽产生明确 error/incomplete，而不是无限排队。
- 工具等待超时时调用 `Future.cancel()`；该操作只会取消尚未开始的 Future。已开始的 Python 调用不被描述为已取消，仍受其下层 HTTP/数据库超时约束。
- 工具工作线程真正开始时重新检查 execution token 和 deadline，因此排队期间暂停、取消、接管或预算耗尽不会再执行工具主体。
- heartbeat 捕获数据库异常并标记 `lost=True` 后退出；`stop()` 会通知并 join 续租线程。调用边界以 `lost` 阻止后续模型/工具调用。
- LLM 重试循环在每次请求、退避前后检查执行权和剩余总预算；每次非流式 HTTP 请求重新取得不大于当前剩余预算的 timeout。

实际验证：
- `.venv/bin/python -m pytest -q tests/unit/test_execution_boundary.py tests/unit/test_tool_task_budget.py tests/unit/test_agent_run_nonblocking.py`：**5 passed**。覆盖 heartbeat 数据库异常失权、工具开始时的 ownership 重检、任务预算和路由非阻塞。

未完成：
- 本轮尚未模拟真实 HTTP 重试耗尽总预算，也未对线程池饱和的多线程竞争作隔离数据库集成验证；下一批统一入口时应补齐。

补充（队列 deadline 修复）：
- 工具容量准入、排队及 Future 等待现在共享同一个绝对 monotonic deadline；成功准入后重新计算剩余时间，不会重新获得完整工具 timeout。
- `/agent/run` 容量等待改为 async 协作轮询；请求协程取消不会遗留后台 acquire。分析容量在实际 Future 完成的回调中释放，而不是 HTTP 请求提前结束时释放；submit 失败会立即释放已取得容量。
- `tests/unit/test_tool_task_budget.py` 的可控阻塞器覆盖队列饱和、准入耗尽同一预算、超时排队任务在容量后来后不执行：本轮 `tests/unit/test_tool_task_budget.py tests/unit/test_execution_boundary.py tests/unit/test_agent_run_nonblocking.py` 为 **6 passed**。

## 21. 入口执行生命周期统一（部分完成）

已完成：
- `AgentExecutionService.start()` 现在按任务语义选择 `conversation`（assistant/有 conversation_id）或 `analysis` run_type，旧行保持可读。
- Worker 的既有 `RunTracking` 改为兼容适配层：创建分析 run 后经执行服务 claim，保存 token，启动 heartbeat；事件、工具记录、失败和 `waiting_review` 收尾都使用 token 约束的执行服务写入。Outbox 的重试和 `RequirementService` 的幂等、人工审核/正式落库边界未改变。
- 问答 ReAct 主路径保留原 Agent 与 SSE 输出方式，但运行行从 queued 原子 claim；heartbeat、工具事件、工具记录、checkpoint 和完成/失败改经执行服务。ChatRepository 仍只处理会话和消息。

实际验证：
- 独立 PostgreSQL 库运行 `.venv/bin/python -m pytest -q tests/integration/test_agent_run_tracking.py tests/integration/test_analysis_tool_wiring_isolated.py`：**23 passed**。包含真实数据库 token 抢占、迟到写入拒绝、heartbeat、分析追踪和待审核终态。
- `.venv/bin/python -m pytest -q tests/unit/test_assistant_agent.py`：**5 passed**；`py_compile` 与 `git diff --check` 通过。

未完成：
- 动态聊天图、resume/clarification 等旧聊天兼容分支尚未逐一迁移到 token 生命周期；本批只迁移问答 ReAct 主路径，不能宣称所有聊天路径已统一。
- 未进行真实 MCP/LLM 联调；SSE 仍由连接协程发送，不是后续批次的后台执行/断线回放实现。

补充（本批聊天动态分支与 Worker 实际执行接入）：
- `RequirementService` 现在将 `RunTracking` 的 token ownership guard 与已持久化的绝对 deadline 传给真实 `run_analysis` 调用；图节点边界失权或预算耗尽会停止后续模型/工具调用，最后一次返回后再次校验所有权才进入 `waiting_review`。Outbox 的原有幂等/审核提交路径未改变。
- 动态需求聊天、澄清补充与 resume 创建的运行均会 claim token 并启动 heartbeat；每个节点 checkpoint 使用 token 条件更新。pause/cancel/接管赢得状态后，旧流不能再写 checkpoint 或完成。
- assistant 问答与动态需求聊天的最终 assistant message 和 run 终态通过同一个 token 行锁事务写入；失权时既不会追加该消息，也不会把 run 伪装为完成。澄清结果以同一方式持久化为 `waiting_clarification`，保留已有暂停/恢复 API 形状。

实际验证：
- 独立 PostgreSQL 库运行 `PYTHONPATH=src /home/wangmengyang/Software/Requirement_agent/Requirement_agent/.venv/bin/python -m pytest -q tests/integration/test_dynamic_chat_checkpoint.py tests/integration/test_run_resume.py tests/integration/test_agent_run_tracking.py`：**20 passed**（含澄清后补充、resume、pause/cancel、Worker token tracking）。

仍未完成：
- 旧四阶段 `_stream_chat_pipeline_legacy` 仍使用 ChatRepository 兼容状态写入，未迁入 token 生命周期；它只服务历史 checkpoint 兼容，不能称为完全统一。
- 本批没有实现逐回合消息/tool 原始结果恢复，也未将 SSE 与连接解耦；外部 LLM/MCP 未联调。

## 22. 聊天收尾与 Worker 发布边界补丁（部分完成）

已完成：
- assistant message 与 run 的原子收尾现合并既有 `agent_run.meta`，不再因写入 `conversation_id` / `assistant_message_id` 丢失 intent、deadline、预算或路由字段。
- 只读问答 run 在创建时持久化 `deadline_at_epoch` 与预算；执行时按剩余时间初始化 ReAct，并将剩余预算写回 checkpoint。预算耗尽的消息仍原子保存用于回放，但 run 保持 `failed`、`meta.outcome=incomplete`，最终事件为 `run_incomplete`，与 pause/cancel/provider failed 区分。
- 动态需求聊天每个图节点事件改为直接使用当前 claim token 落库；生产图获得 execution guard 与绝对 deadline。失权/超时后不再写 checkpoint 或追加后续事件。
- Worker 在来源变为 `pending_review` 前再次检查 token ownership；完成 `waiting_review` 放在业务可见结果发布之后，避免已观察到失权的执行者发布旧计算结果。

实际验证：
- 隔离 PostgreSQL：`tests/integration/test_agent_run_tracking.py tests/integration/test_dynamic_chat_checkpoint.py`：**15 passed**。
- 动态聊天/恢复回归：`tests/integration/test_dynamic_chat_checkpoint.py tests/integration/test_run_resume.py`：**6 passed**。
- ReAct 预算与引用单元回归：`tests/unit/test_assistant_agent.py`：**5 passed**。

仍未完成：
- 历史四阶段 `_stream_chat_pipeline_legacy` 的旧 checkpoint 兼容写路径仍待逐段 token 化；新动态聊天路径不再回退到它，除非读取历史 checkpoint。
- Worker 的 source 更新与 run 终态尚非跨表单事务；本批在发布前增加 ownership fence，下一批应将“来源发布 + waiting_review”做成同一受 token 约束的数据库事务。

## 23. 发布事务原子化、发布前副作用审计与聊天预算传播

已完成：
- **Worker 发布成为单个 token 事务**：新增 `AgentRunRepository.publish_source_and_finish_if_owned`（`agent_run` 行 `FOR UPDATE` + `execution_token`/`status='running'` 谓词），把「来源 → `pending_review` + metadata 回填」「run → `waiting_review`」「最终 `run_completed` 事件」放进同一个提交。`AgentExecutionService.publish_worker_result` 与 `RunTracking.publish_source_result` 是新的入口；`RequirementService._analyze_and_mark_pending` 改为只调用它，失权即整体回滚并抛 `RuntimeError`（返回结构、outbox 幂等与 ReviewService 边界未动）。来源状态 `pending_review` 与 run 状态 `waiting_review` 分属两个状态集，刻意保留为两个参数。
- **发布前持久化副作用审计**：`record_events` / `record_tool_calls` / `checkpoint` 三种写入经核实**本来就在 token 行锁下**（`append_events_if_owned` / `record_tool_invocations_if_owned` / `checkpoint_if_owned`），已加注释并用真库测试钉住「接管后旧 token 全部写不进」。`update_extraction`（输入规整）发生在建 run 之前、不受栅栏保护，但它只写由原文确定性推导的分段/归一化字段与 `processing_status='extracting'`，不含任何分析产物 —— 已注释说明「为什么安全」。
- **发现并修复一处未被点名的陈旧写入**：能力/条件候选比对（`capability_matcher.match`）原先在发布守卫**之前**独立提交 `pending_confirmation` 提案行，被接管的执行者会留下陈旧提案。现改为在发布事务内、以 SAVEPOINT 包裹执行（`_capability_match_hook`），失权则提案随事务回滚，比对自身失败仍降级成 `error=match_failed` 而不拖垮发布。
- **聊天/ReAct 预算真正逐次下传**：助手工具调用 `invoke(..., remaining_seconds=remaining_seconds())`；`_stream_library_assistant` 用 `bind_execution(None, deadline)` 把 run 的绝对 deadline 绑进 ReAct 线程，使 provider 每次请求的 HTTP timeout 与工具等待都按当前剩余预算截断（只绑 deadline、不绑 ownership guard，暂停/取消语义不变）。provider 抛出的 `AnalysisBudgetExhausted` 映射为 `incomplete` 而不是「模型不可用」；`should_stop` 返回 `incomplete` 时也不再输出「已暂停或取消」这句话。
- **续跑不再重置预算**：`_stream_chat_pipeline` 在 `create_run`（按 `client_message_id` 幂等 upsert，会整体覆盖 meta）之前先取既有 run 的 `deadline_at_epoch` 并沿用，只有全新消息或历史 run 没记 deadline 时才发新预算。
- **旧兼容管线路由改为失败关闭**：`_stream_chat_pipeline_legacy` 只接受真正的历史断点（checkpoint 无 `version` 键）。带 `version` 却解不开的动态断点现在直接报错收流，不再退回 ChatRepository 的无 token 写入路径。

实际验证：
- 新增 `tests/integration/test_worker_publish_atomicity.py`：**4 passed**。覆盖「接管恰好落在发布边界 → 来源、终态、能力提案都不落库」「发布事务内部注入失败 → 已执行的来源 UPDATE 一起回滚」「正常路径三件事同事务提交」「旧 token 写不进事件/工具/checkpoint/终态」。前两条在把发布改回 HEAD 的「守卫 + 两步写」时**确实失败**（陈旧提案与陈旧来源被提交），是真实的回归测试。
- 新增 `tests/integration/test_chat_budget_lifecycle.py`：**6 passed**。覆盖聊天预算耗尽（`failed` + `meta.outcome=incomplete` + `run_incomplete` + 消息可回放）、暂停落在节点执行期间（run 保持 `paused`，stage/checkpoint 未被覆写）、续跑沿用原 deadline、解不开的动态断点被拒绝、真历史断点仍走兼容路径、澄清结果的 token 与版本化 checkpoint。
- 基线与回归：改动前 `tests/` 全量 **31 failed, 724 passed, 16 skipped**（失败全部为既有用例假设预置样本/真实 embedding，与本批无关）；改动后 **31 failed, 734 passed, 16 skipped**，失败集合逐条 diff **完全一致**，新增 10 条全绿。
- 定向回归：`tests/integration/test_agent_run_tracking.py tests/integration/test_dynamic_chat_checkpoint.py tests/integration/test_run_resume.py tests/unit/test_assistant_agent.py`：**25 passed**；`py_compile` 与 `git diff --check` 通过。

仍未完成 / 需注意：
- 续跑沿用绝对 deadline 意味着「暂停超过预算时长后再续跑」会立即以 `incomplete` 收尾（而不是再拿一整份预算）。这是「续跑不得刷新预算」的直接后果，但对长时间暂停的用户是行为变化，需要产品侧确认要不要改成「暂停期间不消耗预算」（按暂停时长平移 deadline）。
- 澄清补充走的是**新 user 消息 + 新 run**，因此仍会获得一份新预算；这是有意保留的（新回合新预算），未按父 run 剩余预算扣减。
- Worker 发布失败（非失权，例如发布事务自身报错）时 run 会停在 `running` 等租约过期，尚未在该分支主动写 `failed`；由上层 outbox 任务收尾。
- 旧四阶段兼容管线本身仍是 ChatRepository 无 token 写入（只服务历史断点），未逐段 token 化；SSE 仍由连接协程发送。
