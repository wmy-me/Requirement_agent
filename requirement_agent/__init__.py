"""requirement_agent：顶层兼容包（目标业务导入路径）。

目标导入形态为 `requirement_agent.*`（不把 `src` 当正式业务包名）。为在源码树下
直接使用该路径，本顶层包作为**兼容转发**：`src/requirement_agent/` 是物理实现位置，
`requirement_agent/` 提供等价导入入口。二者指向同一对象（如 `api.app.app`）。

当前只转发 `api.app`（统一 FastAPI 入口）；其余子包将在后续子批次迁移时补充转发。
物理实现与迁移策略见 docs/refactoring/path-mapping.md、docs/refactoring/compatibility-plan.md。
"""
