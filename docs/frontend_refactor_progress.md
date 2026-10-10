# 前端重构实施记录

## F0–F2（2026-10-10）

- 已完成：在 `frontend/` 新建 React + TypeScript strict + Vite 工程，使用 React Router、TanStack Query 与 Lucide；`/app-next` 的路由与 `/app` 旧入口分离。
- 已完成：GPT 风格的共享资料库壳层。一级入口保留新对话、资料库、待审核、任务记录；后两项及聊天在本批显式跳转旧页，未假装接入不稳定的恢复、审核或 SSE。
- 已完成：资料库文档/正式需求标签，严格使用实际 `file_name`、`requirement_name` 等字段；ID 在契约与 URL 中均为字符串，已移除宽泛 `UnknownRecord`。文档详情读取详情/分片；需求详情读取版本、功能、结构化 Diff、结构化来源。版本选择会实际改变 features/Diff 请求。
- 已完成：上传资料调用既有 `POST /api/v1/documents/upload`，成功后刷新文档窗口；最近新增合并真实文档/需求窗口，最近对话读 `GET /api/v1/conversations`，无数据时显示空状态，侧栏折叠有真实状态行为。
- 已完成：FastAPI 以 `/app-next` 提供构建结果、`/app-next/assets` 提供哈希资源，所有非资源子路径回退至 SPA index，继续使用运行时注入的 `ra-ui-token`。`/app` 仍是旧版回退入口。
- 搜索是当前已加载窗口内过滤，UI 显示窗口条数，不标为全库搜索或分页。资料范围问答尚无结构化后端支持，详情页明确说明不能保证限定检索，只链接旧入口。
- 真实接口缺口：文档列表只接受 `limit`，需求列表没有本批前端实际用到的总数/分页契约；因此 UI 不显示虚构 total。文档版本 API 已存在但本轮详情优先实现正文/片段。
- 验证：`npm run typecheck`、`npm run build`、FastAPI 模块编译、`git diff --check` 已通过。隔离库 FastAPI 启动联调仍待完成；不把构建验证误报为真实 API/上传验收。

## 未开始

- F3 对话、F4 审核、F5 任务记录、F6 全量视觉验收。
- 与隔离数据库的文档、需求详情、上传真实路径及带真实数据截图；不在业务库执行。
