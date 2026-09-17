"""L3 层：需求主线的**只读**工具。

分层依据 `docs/分析_工具分层现状与越层调用.md`，本包是其中的 **L3 内部只读 Query/Tool 层**：

```
L1 Repository           数据访问（本包**不直接碰**）
L2 Application Service  编排、事务、正式写入
   └ requirement_query.py  ← 只读查询内核（多数工具封装它）
L3 tools/               本包：薄封装 + 入参校验 + 三态结果 + 超时 + 审计
L4 模型 Adapter         ⬜ 未建（需要时才做）
```

**三条硬约束，都由 `registry.register()` 在注册时校验，不靠纪律：**

1. **只读** —— `read_only=False` 的工具**注册不进去**。需求治理里一切正式写入必须经
   人工评审（`docs/流程_需求从提交到入库.md` §8），L3 不允许有写工具。
2. **裁决类名字不许出现** —— `approve_review` / `commit_requirement` 之类，
   哪怕实现成只读转发也不给注册（名字本身会误导读者）。
3. **必须有 input_model** —— Pydantic 模型是输入校验与 JSON Schema 的唯一事实源。

## ⚠️ 这里**刻意不 import 任何工具模块**

导入一个包里的**子模块**会先执行那个包的 `__init__`。工作流（`workflows/agents_nodes`）
要经 `tools.invoker` 调工具，于是会触发本文件；如果这里一口气 import 全部工具，
就会连带把 `preview_merge_impact → application.review_service → workflows.commit_nodes`
拉进来 —— 而 `workflows` 此时正处在**半初始化**状态，循环导入当场炸
（实测踩到：13 个测试文件收集失败）。

所以注册改成**按需加载**（`registry._ensure_loaded`）：第一次问「有哪些工具」时才 import。
本文件只导出契约与注册表。
"""

from __future__ import annotations

from requirement_agent.tools.base import BaseTool, ToolInput, ToolResult, ToolStatus
from requirement_agent.tools.invoker import (
    ToolInvocationError,
    invoke,
    is_ok,
    tool_call_record,
)
from requirement_agent.tools.registry import (
    FORBIDDEN_NAMES,
    TOOLS,
    create,
    descriptors,
    for_consumer,
    get_class,
    llm_schemas,
    names,
    register,
)

__all__ = [
    # 契约
    "BaseTool",
    "ToolInput",
    "ToolResult",
    "ToolStatus",
    # 注册表（`names()` / `create()` / `descriptors()` 会顺带触发按需注册）
    "FORBIDDEN_NAMES",
    "TOOLS",
    "create",
    "descriptors",
    "for_consumer",
    "get_class",
    "llm_schemas",
    "names",
    "register",
    # 统一调用入口（B3）
    "ToolInvocationError",
    "invoke",
    "is_ok",
    "tool_call_record",
]
