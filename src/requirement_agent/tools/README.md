# 工具目录

工具实现按业务能力归档，每个工具只有一份实现：

- `requirements/queries/`：需求详情、功能、关系、列表和模块查询。
- `requirements/retrieval/`：需求、功能、能力和约束检索。
- `requirements/versions/`：版本查询与版本比较。
- `requirements/sources/`：来源列表与来源链追踪。
- `requirements/analysis/`：抽取、关系分析、风险评估。
- `requirements/previews/`：合并影响预演。
- `external/web.py`：Tavily MCP 的联网只读工具。
- `agents/toolsets.py`：显式 Agent 工具集和唯一加载入口。
- `base.py`、`registry.py`、`invoker.py`：契约、注册和调用边界。

目录只表达组织方式；真正的消费权限仍由工具类的 `allowed_consumers` 和 `invoker` 强制执行。
