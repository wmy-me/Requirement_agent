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
3. **外部能力**：真实 LLM/Tavily 未作为通过条件；聊天协议使用确定性 fake provider 验证。
   没有有效凭据时不能声称真实外部服务通过。

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
