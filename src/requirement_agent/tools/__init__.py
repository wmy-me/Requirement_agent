"""L3 层：需求主线的**只读**查询工具。

分层依据 `docs/分析_工具分层现状与越层调用.md`，本包是其中的 **L3 内部只读 Query/Tool 层**：

```
L1 Repository           数据访问（本包**不直接碰**）
L2 Application Service  编排、事务、正式写入
   └ requirement_query.py  ← 只读查询内核（本包**封装它**）
L3 tools/               本包：薄封装 + 入参校验 + 三态结果 + 审计
L4 模型 Adapter         ⬜ 未建（需要时才做）
```

**与上一版最根本的差别：上一版薄封装的是 Repository（分层错误），这一版封装的是
Query Service。** 工具里不该出现 `from ...db.repositories import ...` ——
跨仓储的组合、业务语义的校验、输出形状的统一，都是 `RequirementQueryService` 的职责。

**三条硬约束，都由 `registry.register()` 在注册时校验，不靠纪律：**

1. **只读** —— `read_only=False` 的工具**注册不进去**。需求治理里一切正式写入必须经
   人工评审（`docs/流程_需求从提交到入库.md` §8），L3 不允许有写工具。
2. **裁决类名字不许出现** —— `approve_review` / `commit_requirement` 之类，
   哪怕实现成只读转发也不给注册（名字本身会误导读者）。
3. **必须有 input_model** —— Pydantic 模型是输入校验与 JSON Schema 的唯一事实源。

**本包没有任何调用方** —— 与上一版一样，批次 2 才接线。但这次的验收标准不同：
**必须有真实调用 `execute` 的测试**（上一版没有，所以能静默腐烂，
见 `docs/分析_工具分层现状与越层调用.md` §3.4）。
"""

from __future__ import annotations

# 导入即注册（`@register` 在 import 时执行）
from requirement_agent.tools.base import BaseTool, ToolResult, ToolStatus
from requirement_agent.tools.get_requirement_detail import GetRequirementDetailTool
from requirement_agent.tools.get_requirement_features import GetRequirementFeaturesTool
from requirement_agent.tools.get_requirement_versions import GetRequirementVersionsTool
from requirement_agent.tools.list_requirements import ListRequirementsTool
from requirement_agent.tools.registry import (
    TOOLS,
    create,
    descriptors,
    for_consumer,
    llm_schemas,
    names,
    register,
)
from requirement_agent.tools.search_requirements import SearchRequirementsTool

__all__ = [
    # 契约
    "BaseTool",
    "ToolResult",
    "ToolStatus",
    # 注册表
    "TOOLS",
    "create",
    "descriptors",
    "for_consumer",
    "llm_schemas",
    "names",
    "register",
    # 工具（本名录即注册表清单，改动要同步 tests/unit/test_tool_contract.py）
    "GetRequirementDetailTool",
    "GetRequirementFeaturesTool",
    "GetRequirementVersionsTool",
    "ListRequirementsTool",
    "SearchRequirementsTool",
]
