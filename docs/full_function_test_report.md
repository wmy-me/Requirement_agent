# 全功能测试与验收报告（2026-10-12）

基线：`master / 3f79e8d`；本轮在全新隔离库 `requirement_agent_fulltest_20261012` 执行。
该库由 `migrations/*.sql` 按文件顺序迁移，测试角色为非超级用户；未连接或清理
`requirement_agent` 业务库。临时认证仅用于本轮进程，交付前撤销。

| 功能 | 证据/测试 | 结果 |
|---|---|---|
| 迁移与隔离入口 | 全量 migrations 输出、独立 DB/角色 | 通过 |
| 审核提交/预演/版本 | `test_review_commit_path.py`、`test_merge_preview.py` | 此前隔离组 21 passed；全量待自包含化复验 |
| 聊天终态、预算、暂停、澄清、恢复 | chat lifecycle/dynamic checkpoint/run tracking | 此前隔离组通过；全量执行中发现历史夹具依赖 |
| 鉴权、需求、文档、Worker、outbox、memory/capability | 全量 pytest | 已执行；见“缺陷” |
| 前端 SSE/审核规则 | `npm run test` | 9 passed（上轮） |
| 类型与构建 | `npm run typecheck`、`npm run build` | 通过（上轮） |

## 缺陷与限制

1. **测试缺陷（已修）**：`test_workbench_endpoints.py` 的“committed 来源能关联正式需求”和
   “审核历史”原先依赖开发库预置数据。现改为 fixture 通过真实 ReviewService 创建、提交并清理
   committed 来源，保留原断言含义。
2. **测试缺陷（待系统修复）**：全量 pytest 在全新空迁移库中仍出现多处失败和 skip，表明其余
   历史集成测试也依赖开发种子或跨测试顺序。没有新增 skip 或放宽断言；应逐个迁为自建/清理夹具。
3. **外部能力（2026-10-12 复验）**：以当前已配置的 DeepSeek provider 发送最小无业务
   prompt，服务端返回 HTTP 401；因此 LLM 凭据无效或已失效，真实 LLM 联调**失败**。
   Tavily MCP 当前为禁用状态，且 `TAVILY_API_KEY` 未配置，按安全配置未发起请求，真实 Tavily
   联调**未执行**。两侧本地协议由 `test_tavily_mcp.py`、`test_model_registry.py` 与
   `test_agent_chat_narrative.py` 共 50 项确定性测试覆盖并通过；不得将其表述为真实外部服务通过。
   另发现当前默认配置的模型调用观测库不是隔离库：该探针没有创建 agent run、审核或需求写入，
   但在为真实联调准备隔离 `POSTGRES_*` 配置前，不得把它称为完全隔离的端到端外部测试。

## 可复跑命令

```bash
POSTGRES_DB=requirement_agent_fulltest_20261012 \
POSTGRES_USER=<isolated-role> POSTGRES_PASSWORD=<injected-secret> \
API_AUTH_TOKEN=test-api-token TAVILY_MCP_ENABLED=false \
.venv/bin/python -m pytest -q

cd frontend && npm run test && npm run typecheck && npm run build
```

截图：`docs/design/screenshots/2026-10-12-workbench-desktop.png` 与
`docs/design/screenshots/2026-10-12-workbench-narrow-390.png`。它们验证现有黑白灰工作台壳层；
真实 API 全页面浏览器流仍需在自包含业务夹具完成后复拍。

## 隔离验收复测（2026-10-12，HEAD `d649c68` 后）

### 环境与写入边界

- 新建专用库 `requirement_agent_audit_20261012`，迁移按 `migrations/*.sql` 顺序执行；
  `vector` 扩展由数据库管理员在**该库**创建，运行期使用非超级用户
  `audit_runner_20261012`。运行时探针确认 `current_database()` 为该库、角色为该用户，
  pgvector 已启用。
- API 以同一组隔离 `POSTGRES_*` 配置启动，`OUTBOX_CONSUMER_ENABLED=false`；模型调用
  recorder 在真实 embedding 探针前显式关闭。没有读取、清空或写入 `requirement_agent`
  业务库，也没有关闭鉴权或 TLS 校验。
- 合成资料仅为 “synthetic embedding acceptance material” 及测试生成的随机标识；写入的
  requirement/vector 在 `finally` 中删除。

### 实际接口清单与覆盖

本地 FastAPI 的 `/openapi.json` 实测为 **80 path / 87 method+path operation**（GET、POST、PUT、
DELETE；清单生成于 `/tmp/requirement_agent_audit_api_inventory.txt`）。本轮用真实 HTTP 验证
`/health`、OpenAPI、旧 `/app`、新 `/app-next`、新前端深链接、需求/审核列表以及未授权请求：
前者均为 200，两个未带 token 的读取请求均为 401。鉴权、审核、聊天、运行控制、上传、文档、
Outbox、Worker、版本、关系等由下述集成测试覆盖。

未逐条手工构造 87 个操作的正常/非法/不存在/权限/回滚组合，不能将接口清单视为 87/87 手工
验收完成；尚未覆盖的 multipart 边界、CSV/HTML 静态资源细节及每个错误码组合保留为未完成项。

### 回归结果

| 组别 | 结果 | 说明 |
|---|---:|---|
| `tests/unit` | 391 passed, 2 skipped | 修复后全绿；skip 未新增 |
| 核心集成（鉴权、审核、聊天、Outbox、Worker、上传） | 79 passed | 隔离库真实运行 |
| 其余集成 | 270 passed, 27 failed, 18 skipped | 失败均见下方，未用 skip 掩盖 |
| 前端 | 9 passed；typecheck/build 通过 | `npm run test && npm run typecheck && npm run build` |

集成合计为 **349 passed, 27 failed, 18 skipped**；全仓按已执行分组累计为
**740 passed, 27 failed, 20 skipped**。完整 `pytest -q` 曾在执行环境的 30 秒托管窗口中被中断，
因此该累计来自覆盖全部 37 个集成文件与 unit 目录的分组执行，不能伪称为一次单进程完整命令的
最终汇总。

### 本轮修复

1. `test_dynamic_requirement_graph.py` 的假调用此前把所有注册表工具都伪装成检索结果，导致
   extract 缺少必填字段；改为按 `extract_requirement`、`search_requirements`、分析、风险四种
   工具返回对应真实契约。
2. `test_requirement_graph.py` 已经过注册表边界调用，但旧替身仍按旧 Agent 签名提供；补齐
   `analysis_mode` 并接线到四个受注册表保护的确定性替身。
3. `test_retrieval_service.py` 不再依赖业务库 `REQ-000001`：测试自行创建 requirement、写入
   4096 维 pgvector、检索、断言余弦并清理。

### 外部服务

| 能力 | 真实结果 | 证据/限制 |
|---|---|---|
| DeepSeek chat | blocked / HTTP 401 | 已配置 endpoint 的最小合成 prompt 被服务端拒绝；需要检查 `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL`，未替换模型或把重试当成功 |
| Embedding | **通过** | `Doubao-embedding`，endpoint host `oneapi.pateo.com.cn`；真实合成文本返回 4096 个有限数值，写入隔离 `requirement_embedding` 后可由 pgvector 查回，模型/维度来源正确 |
| Tavily MCP | blocked / not_configured | `TAVILY_MCP_ENABLED=false` 且无 `TAVILY_API_KEY`；按现有 MCP 适配器不发请求，不改走 REST。启用后需该两项配置才可测发现/search/extract |

### 浏览器证据与待完成项

隔离 API 的真实截图：
`docs/design/screenshots/2026-10-12-audit-app-next-desktop.png` 与
`docs/design/screenshots/2026-10-12-audit-app-next-390.png`。两张分别是 1440px 与 390px 的
`/app-next`。它们不等同于八个页面的完整有数据交互验收；当前空库的多项历史集成测试仍硬编码
开发种子（`REQ-000015`、能力词表、来源/关系/版本时间轴），共导致上述 27 个失败。应将这些
测试逐项迁为自建/清理 fixture，才能完成真实 API 八页面、有数据导航和每一接口的错误矩阵验收。

### 后续本机外部研究复测（2026-10-12）

- DeepSeek 配置修正后，以公开合成问题走真实 `/api/v1/agent/chat/stream`：意图为
  `external_research`，`web_policy=auto`，run 最终为 `completed`。测试会话已在验证后删除。
  这一次使用的是开发服务库，**不是**隔离验收库，故仅证明外部模型与协议链路，不能替代隔离库验收。
- 模型真实发出了 `web_search`，工具审计返回 `error: Tavily MCP is disabled`，来源数为零；
  页面会显示本轮实际工具调用，且不会虚构网站来源。当前仍需在本机环境设置
  `TAVILY_MCP_ENABLED=true` 和 `TAVILY_API_KEY` 后，才能复测真实 tools/list、搜索、网页提取
  以及可点击来源卡片。
- 聊天 UI 与 API 默认 `web_policy` 统一改为 `auto`。自动模式只为外部调研意图开放外部只读工具；
  普通需求库问答不会被无条件发送到外部服务。前端回归为 10 passed，类型检查和构建通过；后端
  schema/assistant/narrative 相关回归为 15 passed。

### Tavily MCP 真实复测（2026-10-12，后续配置完成后）

- `.env` 的 `TAVILY_MCP_ENABLED=true` 与凭据均已配置。发现客户端只读 `os.environ`，未读取项目
  已约定的 `.env`，修复为进程环境优先、`.env` 本地兜底；凭据未写入日志、响应或测试输出。
- Tavily 当前远端实际工具名为 `tavily_search` / `tavily_extract`，项目旧的连字符映射导致发现结果
  与白名单无交集。已切换到现行下划线名称，并兼容旧 `.env` 的连字符允许列表。
- 真实 MCP 验证通过：工具发现得到上述两个只读工具；公开查询 `Python official documentation`
  返回 2 条结果；对 `https://docs.python.org` 的公开网页提取成功且有正文。
- 真实 FastAPI + DeepSeek + Tavily 聊天流验证通过：外部研究意图、`web_policy=auto`，SSE 终态
  `completed`；工具审计为 `web_search` 成功 2 次、`read_web_page` 成功 1 次，artifacts 返回
  11 条公开来源 URL。该公开合成会话已通过会话删除接口清理。前端会把这些 `sources` 自动渲染为
  答案下方可点击的“相关资料来源”，并把三次调用渲染为“本轮实际工具调用”。

### 文档分片可见性补齐（2026-10-12）

- 对业务库只读核对：`document_asset` 当前有两篇文档，`document_chunk` 有 7 条；PDF 为 6 片，
  `agent-matrix.txt` 为 1 片。两篇均为 4096 维 `Doubao-embedding`，对应两条
  `document_chunk_sync` Outbox 都是 `completed`。
- 新增文档详情读模型：分片分页返回总数、原始 `chunk_text`、序号、字符数、hash 与 embedding
  provenance；`index-status` 从真正的 chunks 与最近 outbox 聚合，避免把版本状态当作索引状态。
  浏览器实测 PDF 显示 6 个原始 Chunk、`ready` 和 `Doubao-embedding · 4096 维`。
- 同时接入已有大文件断点续传接口；本轮未向业务库上传测试文件。前端编译与既有测试通过，
  后端文档读契约由确定性单元测试覆盖。断点续传写入验证仍应在隔离库完成。
