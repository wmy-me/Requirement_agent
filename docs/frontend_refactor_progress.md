# 前端重构实施记录

## F0–F2（2026-10-10）

- 已完成：在 `frontend/` 新建 React + TypeScript strict + Vite 工程，使用 React Router、TanStack Query 与 Lucide；`/app-next` 的路由与 `/app` 旧入口分离。
- 已完成：GPT 风格的共享资料库壳层。一级入口保留新对话、资料库、待审核、任务记录；后两项及聊天在本批显式跳转旧页，未假装接入不稳定的恢复、审核或 SSE。
- 已完成：资料库文档/正式需求标签，读取 `GET /api/v1/documents` 与 `GET /api/v1/requirements`；文档详情读取详情/分片；需求详情读取版本、功能、Diff、溯源。搜索是当前已加载窗口内过滤，未标为全库搜索或分页。
- 鉴权：运行时从既有 HTML 的 `meta[name=ra-ui-token]` 读取，未写入 Vite 环境变量或源码。开发时 `/api` 代理到 `VITE_API_ORIGIN` 或 `127.0.0.1:8000`。
- 真实接口缺口：文档列表只接受 `limit`，需求列表没有本批前端实际用到的总数/分页契约；因此 UI 不显示虚构 total。文档版本 API 已存在但本轮详情优先实现正文/片段。
- 验证：`npm run typecheck`、`npm run build` 均通过；本地 Vite 截图为 `/tmp/requirement-agent-frontend-shots/library-desktop.png`（1440px）和 `/tmp/requirement-agent-frontend-shots/library-mobile.png`（390px）。截图验证壳层与窄屏布局；真实 API 联调需要可用 FastAPI 与运行时令牌，未在业务库执行。

## 未开始

- F3 对话、F4 审核、F5 任务记录、F6 全量视觉验收。
- 静态产物由 FastAPI 在 `/app-next` 服务、生产深链接回退配置；需在后续部署接入批次完成。
