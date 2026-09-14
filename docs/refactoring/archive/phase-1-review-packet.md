# 阶段 1 复核材料（结构重组）— 提交技术顾问审查

> 生成日期：2026-09-11 ｜ 复核范围：`962fab5`（阶段1 前）→ `dc91f43`（HEAD）
> 依据：当前工作区实际代码 + git 历史。**本材料所有结论均可按 §6 命令独立复现。**

---

## 1. 复核目的

确认「项目结构重组」（总体施工顺序的阶段 1）**只改了结构、未改行为**，且旧代码删除是安全的。

## 2. 交付物

### 2.1 提交链（6 个 commit，工作区干净）
```
dc91f43  docs: 记录 src-layout 转换（阶段1 收尾）
17379f4  阶段1 补：转为真正 src-layout（导入 requirement_agent.*）
5d2d551  阶段1 结构重组：其余分层全部迁入 requirement_agent
180578b  阶段1 收尾：迁移依赖/剩余Schema/聚合器，删除旧 src/interfaces
1f88419  阶段1 结构重组：全部 HTTP 路由迁入 api/routes
df44749  阶段1 结构重组：包骨架 + API 路由迁移；移除 MCP 服务
```
聚合规模：**123 文件改动，+2497 / −1231**。

### 2.2 最终结构（src-layout）
```
Requirement_agent/
├── main.py                 # 入口薄壳 → requirement_agent.api.app（uvicorn main:app :8888）
├── apps/                   # api（薄壳）/ worker（后台入口）
├── src/
│   └── requirement_agent/  # ★ 正式业务包（导入名 requirement_agent.*）
│       ├── api/  application/  agents/  skills/  workflows/  domain/
│       ├── infrastructure/  common/  config/  tools/  workers/
├── migrations/ deploy/ static/ scripts/ tests/ docs/ docx/
```
`src/` 顶层**只剩 `requirement_agent/`**。

## 3. 不变性证据（核心）

| 维度 | 结论 | 证据 |
|---|---|---|
| **HTTP 路由总数** | 42（迁移前=迁移后） | §6 命令 1 |
| **路由注册顺序** | 逐条一致 | §6 命令 1 |
| **OpenAPI**（components + operationId + 路径 + tags） | **完全一致** | §6 命令 2 |
| **函数体内容** | 逐字一致（仅包路径/重命名差异） | §6 命令 3 |
| **测试基线** | 单测 42/2、全量 43/2（迁移前=迁移后） | §6 命令 4 |
| **入口可用** | `main` / `apps.api.main` / `apps.worker.tasks` / `scripts` 均正常 | §6 命令 5 |
| **PyCharm 解析** | IDE 无 error（src 源码根与 src-layout 一致） | §6 命令 6 |

## 4. 有意的变更（**非迁移副作用**，需顾问知悉）

| 变更 | 文件 | 原因 |
|---|---|---|
| 删除 MCP 服务与配置 | `apps/mcp/`、`src/interfaces/mcp/`、`docker-compose.yml` 的 mcp 服务、`settings` 的 `mcp_port/mcp_auth_token/mcp_issuer_url/mcp_resource_url/require_mcp_auth`、`pyproject` 的 `mcp` 依赖 | 用户指示「不需要 MCP 服务，转为方法」 |
| MCP 工具 → 内部方法 | 新增 `requirement_agent/tools/`（7 方法，函数体逐字保留） | 同上 |
| `mcp_actor_id` → `tool_actor_id`（默认 `tool-client`） | `settings.py` | 彻底去 MCP；影响 `submit_review_decision` 的 reviewer_id |
| Prompt/服务层注释去 MCP | `application/requirement_service.py`（2 处 docstring）、`api/routes/agent.py` | 同上 |
| `graph` → `workflows` | `src/requirement_agent/workflows/` | 目标结构命名 |
| 导入去 `src.` 前缀 | 全仓 79 文件 | 转 src-layout（用户选定） |

> **注意**：`settings.py` 与 `requirement_service.py` 是本阶段唯一两处「非纯迁移」的内容差异，且均为上表中的**有意变更**（已用 diff 逐一确认，无其他改动）。

## 5. 删除清单（均可从 git 历史恢复）

| 已删 | 说明 |
|---|---|
| `src/interfaces/`（整包：http + 空包） | HTTP 层迁至 `requirement_agent/api/` |
| `apps/mcp/`、`src/interfaces/mcp/` | MCP 移除 |
| `src/{domain,common,config,infrastructure,application,agents,skills,graph}/__init__.py` 等旧分层 | 迁至 `requirement_agent/` 对应子包 |
| 顶层 `requirement_agent/`（shim） | src-layout 后与 `src/requirement_agent` 冲突，删除 |

## 6. 独立复核步骤（技术顾问可直接执行）

```bash
# 0. 环境：确保已 pip install -e .（src-layout）
.venv/bin/pip install -e .

# 1. 路由总数 + 顺序
.venv/bin/python -c "
from requirement_agent.api.app import app
from fastapi.routing import APIRoute
def fl(rt,o):
    if type(rt).__name__=='_IncludedRouter': rt=rt.original_router
    for r in getattr(rt,'routes',[]):
        (o.append((r.path,sorted(r.methods or []))) if isinstance(r,APIRoute) else fl(r,o))
    return o
p=fl(app,[]); print(len(p), [x[0] for x in p[:10]])"
# 期望：42 条；前 10 = /, /health, health/db, health/llm, requirements, submit, ingest, search, features/search, documents

# 2. OpenAPI 快照（与迁移前对比）
.venv/bin/python -c "
from requirement_agent.api.app import app
import json; s=app.openapi()
print(sorted(s['components']['schemas'].keys())); print(len(s['paths']))"
# 期望：12 个组件（AgentChatRequest…ValidationError），36 条 path

# 3. 内容逐字（抽样，仅归一化包路径）
#    对比 962fab5:<旧路径> 与 <新路径>，抹掉 src./requirement_agent. 前缀后应完全一致
git show 962fab5:src/infrastructure/db/repositories/requirement.py > /tmp/o.py
diff <(sed 's/\bsrc\.//g' /tmp/o.py) <(sed 's/\brequirement_agent\.//g' src/requirement_agent/infrastructure/db/repositories/requirement.py)
# 期望：无输出（完全一致）

# 4. 测试
.venv/bin/python -m pytest tests/unit -q   # 期望 42 passed, 2 skipped
.venv/bin/python -m pytest -q              # 期望 43 passed, 2 skipped

# 5. 入口导入
.venv/bin/python -c "import main, apps.api.main, apps.worker.tasks; print('ok')"

# 6. 旧路径零残留
grep -rnE "src\.(interfaces|domain|config|infrastructure|application|agents|skills|graph)\b" src apps main.py tests scripts --include="*.py"
# 期望：无输出
```

## 7. 已知问题 / 局限（须顾问裁决）

| 级别 | 问题 | 现状 |
|---|---|---|
| P1 | **HTTP 层无鉴权**（`API_AUTH_TOKEN` 未生效） | 重构前即存在；未在阶段 1 处理（属阶段 6 RBAC） |
| P2 | `/health` **重复注册**（`routes/system.py` 与 `api/app.py` 各一个），OpenAPI 有 duplicate operationId warning | 重构前即存在；未改（改了会变 OpenAPI） |
| P2 | OpenAPI tags 双层重复（`['requirements','requirements']`） | 重构前即存在；为保持 tags 一致而**刻意保留**两级聚合 |
| P2 | `apps/api/`（薄壳）、`apps/worker/`（在根，非 src-layout 内） | 用户曾要求保留；`apps` 依赖 cwd 在路径上，未纳入 src-layout 安装 |
| P3 | `apps/mcp` 删除后，PyCharm workspace 里残留 `agent_call_mcp`/`check_mcp_tools` 两个失效运行配置 | 个人配置，未入库，可手动删 |
| P3 | `src/storage/`（空目录）、`src/requirement_agent.egg-info`（安装产物） | 无害残留 |
| 遗留 | `requirement_key`（REQ-000001）为业务友好编号，仍用序列生成；雪花改造后**未提交的 3 文件**已在 `962fab5` 补入 | 已解决 |
| 遗留 | 3 张向量表用 `vector(4096)` 且**无向量索引**（pgvector 上限 2000） | 依赖精确扫描；属阶段 2 检索整改 |

## 8. 待顾问确认的问题

1. `apps/`（api 薄壳 + worker）是否也应迁入 `src/requirement_agent/`（目标结构的 `workers/`）？现留在根、依赖 cwd。
2. `requirement_key` 是否也要去序列化（与雪花 id 一致化）？现仍是 `REQ-000001`。
3. §7 的 P2（`/health` 重复、tags 重复）是否授权在后续阶段一并**修正**（会变更 OpenAPI，需明确授权）？
4. 阶段 2 的整改范围与优先级（见 `docx/LLM_Agent_Prompt_Skill_审计报告_2026-09-11.md`）。

---

## 附：阶段 1 结论

- **结构**：业务代码全部收拢到 `src/requirement_agent/`（src-layout，导入 `requirement_agent.*`）。
- **行为**：HTTP 契约（42 路由 / OpenAPI / tags）、测试基线**零变化**；函数体逐字一致。
- **旧代码**：`src/interfaces/`、旧分层、MCP、顶层 shim 已删除，均可 git 恢复。
- **可运行**：`main:app`（:8888）、worker（:8200）、PyCharm 运行配置与源码根均已就绪。
