# 阶段 0 · 前端基线冻结报告

> **生成日期**：2026-09-18
> **工作区**：`/home/wangmengyang/Software/Requirement_agent/Requirement_agent`（分支 `master`）
> **事实源**：**当前工作区实际文件**。不引用记忆、不引用旧报告。
> **本阶段做的事**：只读 + 实测 + 生成报告。**未修改、未删除、未移动任何前端代码。**

---

## 1. 冻结声明

**从本报告起，以下文件进入冻结，重构期间一行都不改：**

| 文件 | 行数 | 状态 |
|---|---|---|
| `static/index.html` | 160 | **冻结** |
| `static/css/styles.css` | 510 | **冻结** |
| `static/js/app.js` | 1997 | **冻结** |

冻结的含义：新工作台在**别处**建设（见 §5），通过 `/app/*` 与 `/ui` **并存**访问。
**替换完一个页面才删一个**，旧前端在阶段 15 之前始终可用、始终是回退路径。

---

## 2. 旧前端（`/ui`）现状

### 2.1 结构

```
static/index.html        160 行   左栏会话列表 │ 中间对话 │ 右栏工作台
static/css/styles.css    510 行
static/js/app.js        1997 行   全部逻辑（含 7 个 renderXxx 函数）
```

**形态**：对话为中心，工作台是右侧的 5 个 tab（待办 / 需求库 / 文档库 / 审计 / 运维），
外加右下角一个需求库浮层。

### 2.2 接口调用点（重构后要逐一替换的清单）

| 指标 | 数量 |
|---|---|
| `apiJson()` 调用点 | **31** |
| 裸 `fetch()` 调用点 | **4**（3 处 SSE/续跑 + 1 处，见契约 §1.2） |
| `renderXxx()` 顶层渲染函数 | 7 |
| `EventSource` 使用 | **0**（SSE 走 `fetch` + `ReadableStream` 手动解析） |

**实测覆盖的端点**（从 `app.js` 里提取的实际 URL，共 19 个）：

```
/api/v1/requirements?limit=                     /api/v1/requirements/
/api/v1/requirements/submit                     /api/v1/requirements/relations/
/api/v1/reviews/pending?limit=30                /api/v1/reviews/submit
/api/v1/reviews/{}/merge-preview?{}
/api/v1/documents?limit=50                      /api/v1/documents/
/api/v1/conversations?limit=100                 /api/v1/conversations/
/api/v1/agent/chat/                             /api/v1/agent/runs/
/api/v1/ops/outbox?limit=20                     /api/v1/ops/outbox/dead-letters/{}/{}
/api/v1/audit/events?limit=15                   /api/v1/feature-capabilities
/api/v1/health/db                               /api/v1/health/llm
```

> 对照后端实际存在的 **73 条路由** —— 旧前端只用了其中约 1/4。
> 新工作台要用到的端点数量会**翻几倍**（见 `frontend-api-mapping.md` §2）。

### 2.3 已知的结构性问题（决定了「不能渐进改它」）

1. **没有路由** —— 所有状态在一个页面里，靠 `state.sessionId` 与 tab 切换。
   **没法把「这条需求的审核页」发给人。**
2. **没有数据层** —— 31 处 `apiJson` 各自处理加载/错误/刷新，同一个端点在不同地方被重复拉。
3. **1997 行单文件** —— 在这上面叠加 8 个模块只会得到一个 5000 行文件。

---

## 3. ⚠️ 新前端（`static/app/`）现状 —— **这是本次基线最重要的发现**

磁盘上**已经有一套新工作台的雏形**，且**全部处于 git 未跟踪状态**（`?? static/app/`）。

### 3.1 清单

| 文件 | 行数 | 状态 | 说明 |
|---|---|---|---|
| `static/app/index.html` | 20 | ✅ **可用** | 总览页，含 token 注入占位 |
| `static/app/js/core.js` | **295** | ✅ **完整** | 请求层 + 错误分支 + 格式化 + `el()` 防 XSS + 表格/徽标/卡片/三态/抽屉/指标块 |
| `static/app/js/shell.js` | 52 | ✅ **完整** | 左侧导航（8 个一级入口） |
| `static/app/js/pages/index.js` | 106 | ✅ **完整** | 总览页（6 个指标 + 4 张分布卡 + 趋势图 + 健康） |
| `static/app/{requirements,reviews,versions,analysis,knowledge,intake,ops}.html` | 各 20 | ⚠️ **占位** | 有导航外壳，**正文空白** |
| `static/app/css/app.css` | 269 | ✅ | 全套样式 |

### 3.2 🔴 7 个占位页实际是**坏的**

那 7 个 HTML 都加了这行：

```html
<script src="/static/app/js/pages/reviews.js"></script>
```

而 `static/app/js/pages/` 目录下**只有 `index.js` 一个文件**。
实测：

```
缺 static/app/js/pages/{requirements,reviews,versions,analysis,knowledge,intake,ops}.js
```

**后果**：打开 `/app/reviews` 等 7 个页面，浏览器控制台报 404，
左侧导航（`shell.js`）正常渲染，**右侧正文区完全空白、且不报错**。
「页面打不开但也不报错」是最难排查的一类故障 —— 使用者只会说「页面是白的」。

> **所以「新工作台已经做好了」是错觉**：8 个页面里**只有总览是真的**。

### 3.3 后端接线（已完成，不需要动）

| 事项 | 位置 |
|---|---|
| 页面路由 `/app` 与 `/app/{page}` | `api/app.py:238-248`，**白名单**（8 个页名），不做路径拼接（防目录穿越） |
| token 注入 HTML | `api/app.py:203-231`，替换 `<!-- RA_UI_TOKEN_INJECT -->` |
| 鉴权豁免 | `api/auth.py:93` 的 `EXEMPT_PREFIXES` 含 `/app/` |
| Worker 状态端点（缺口 #5） | `api/routes/ops.py` → `/ops/worker` |

---

## 4. 基线验证（本阶段实测）

| 项 | 结果 | 命令 |
|---|---|---|
| 测试套件 | ✅ **695 passed, 2 skipped**，58.5s | `.venv/bin/python -m pytest -q` |
| 后端可导入 | ✅ | `from requirement_agent.api.app import app` |
| 活路由数 | **73 条** / 67 个路径 | `app.openapi()` |
| 真实库连通 | ✅ | `GET /api/v1/health/db` → `{"database": true}` |
| 鉴权可用 | ✅ `settings.frontend_token()` 非空 | `TestClient` 带 Bearer 打真实库 |
| 契约自检脚本 | ✅ 退出码 0 | `python scripts/verify_api_contract.py` |

> ⚠️ **测试全绿不等于接口没问题**：本次盘点在测试之外抓到 **8 处契约与实现的差异**
> （见 `frontend-api-mapping.md` §5 与 §8），其中 2 处是 P0。
> 原因是仓库自带的 `verify_api_contract.py` 的 SPEC **只覆盖 10 个端点**，
> 而问题恰好出在它没覆盖的 `/agent/runs` 与 `/memory` 上。

### 4.1 Git 状态快照

```
分支：master
已修改（未提交）：src/requirement_agent/api/app.py、src/requirement_agent/api/auth.py
未跟踪：          static/app/（整套新前端雏形）
最近提交：        f364412  09-17 17:57  feat(api): 前端重构前置——补齐 6 个工作台端点
```

### 4.2 已过期、不应再作为依据的产物

| 文件 | 生成时间 | 问题 |
|---|---|---|
| `docs/baseline/routes.json` | 09-17 10:57 | **缺** `stats/overview`、`sources`、`reviews/history`、`ops/models`、`ops/worker`（17:57 的 `f364412` 才加） |
| `docs/baseline/openapi.json` | 09-17 10:57 | 同上 |

**处置**：未删除。本次另存了当前事实 `docs/baseline/routes_live.json`（73 条），**以它为准**。

---

## 5. ⚠️ 与既定方案的三处冲突（需要你知晓）

### 5.1 技术选型：方案文档写的是 React，本次要求是原生

`docs/方案_前端工作台.md`：

- **§6.1**：「技术选型（**已定：React + TypeScript + Vite**）」
- **§6.1 注**：明确判断方案 C（纯静态 vanilla）「**本方案要的规模下会失控**」「**C 不可行**」
- **§9**：「~~技术选型~~ —— **已定：React + TypeScript + Vite**」
- **§6.2**：目录结构是 `web/`，含 `.tsx`、`Provider`、`stores/`

**本次要求相反**：不使用 React/Vue/Vite/Webpack/TypeScript，用原生 ES Modules，不加构建流程。

> **你的指令优先，按原生实施。** 但该文档的选型结论**已成为错误信息** ——
> 建议在阶段 2 一并更正，否则下一个人会照它做。
>
> 附带影响：方案 §6.2 的 `web/` 目录、`routes.tsx`、`PermissionGate` 组件等**全部不适用**，
> 阶段 2 的目录结构以本次确认的方案为准（见 §5.2）。

### 5.2 目录结构：你给的树在磁盘上不存在

你给出的树（`static/assets/`、`static/requirements/`、`static/intake/` …）**尚未创建**。
磁盘上的实际结构是 `static/app/*.html`（扁平，8 个页面）+ `static/app/js/{core,shell}.js`。

**已确认的落点**（2026-09-18 拍板）：

- **目录**按你给的树分层：`static/assets/`（公用 css/js）+ `static/<模块>/<页面>.html`
- **URL 留在 `/app/*`**：`/app/requirements`、`/app/reviews/detail`
- 后端改动：把 `api/app.py` 的 `_WORKBENCH_PAGES` 白名单从「单段」扩成「嵌套路径」
- 鉴权：`api/auth.py:93` 的 `/app/` 前缀豁免**已经覆盖**嵌套路径，**不用改**

### 5.3 存量代码：在 `core.js` 基础上拆分，不重写

`static/app/js/core.js`（295 行）**已经实现了本次要求的前 5 个阶段里的大部分**：

| 本次要求的文件 | `core.js` 里已有的对应物 | 状态 |
|---|---|---|
| `http.js` | `request()` + `describeError()`（**已按 401/403/404/409/422 分支**，不解析 detail） | ✅ 可拆 |
| `api.js` | `const api = {get, post, patch}` | ⚠️ 只包了 3 个方法，端点**未逐个声明** |
| `format.js` | `fmt.{time, rel, num, cut}` | ✅ 可拆 |
| `escape.js` | `el()`（**全程 `textContent`，不碰 `innerHTML`**） | ✅ 可拆 |
| `components/*` | `table / badge / card / state / load / drawer / kv / metric` | ✅ 可拆 |
| `layout.js` | `shell.js`（导航 + 高亮） | ✅ 已独立 |
| `pages/dashboard.js` | `pages/index.js`（总览页） | ✅ 可改名 |

**决定：在其基础上拆分。** 保留其中已被验证的实现细节 ——
401/403/404/409/422 的分支文案、`el()` 的防 XSS 写法、三态（加载/空/错误）分离、
抽屉的 Esc 处理。重写同一套东西只会再踩一遍同样的坑。

**但有一处必须改**：`api.js` 目前只有一个泛化的 `api.get(path)`，
**端点路径散在各个页面里**。本次要求「所有 API 请求统一封装到 api.js」——
那意味着**每个端点一个具名函数**，页面不再出现字面量路径。

---

## 6. 本阶段产出

| 文件 | 说明 |
|---|---|
| `docs/refactoring/phase-0-baseline.md` | **本文件** —— 基线冻结报告 |
| `docs/refactoring/frontend-api-mapping.md` | **接口映射表** —— 逐端点参数、响应字段、就绪度、实测证据 |
| `docs/baseline/routes_live.json` | 当前 73 条路由的机器可读清单（**新**，替代已过期的 `routes.json`） |

**本阶段未修改任何前端代码，未删除任何文件，未提交任何改动。**

---

## 7. 进入阶段 1 的前置条件（已满足）

| 条件 | 状态 |
|---|---|
| 旧前端冻结、有回退路径 | ✅ `/ui` 保持可用 |
| 后端接口盘点完成 | ✅ 73 条路由，23 个 GET 端点实测通过 |
| 接口映射表产出 | ✅ `frontend-api-mapping.md` |
| 测试基线 | ✅ 695 passed, 2 skipped |
| 目录与 URL 方案已拍板 | ✅ §5.2 |
| 存量代码处置已拍板 | ✅ §5.3 |

**未决事项** 6 条，见 `frontend-api-mapping.md` §6 ——
其中 2 条（`/agent/runs` 的 number 型 id、`memory` 删除路径）**建议在开工前让后端修掉**，
其余 4 条可以在对应阶段处理。
