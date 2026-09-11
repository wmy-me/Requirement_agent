"""内部 Tool 方法集（可直接调用的普通方法）。

按域拆分：
- `tools.health`      —— 健康检查
- `tools.requirements` —— 需求提交 / 检索 / 详情 / 版本 / 主需求列表
- `tools.reviews`     —— 人工审核提交
- `tools._deps`       —— 共享的服务 / 仓库单例

原则：工具只做「入参 → 领域服务 / 只读 repo」的转发，**不在此实现业务、不直接写库**。
- 写类能力（需求提交、审核落库）一律走 `RequirementService` / `ReviewService`
  （内部经 LangGraph 分析与审核流程，含审计 / outbox）。
- 如需「新增正式需求」，走 `submit_requirement`（进入待审），
  在人工审核通过后才会写入 requirement_master —— 不提供绕过评审的直接写主表方法。
"""

from __future__ import annotations

from requirement_agent.tools.health import health_check
from requirement_agent.tools.requirements import (
    get_master_requirements,
    get_requirement_detail,
    get_requirement_versions,
    search_requirements,
    submit_requirement,
)
from requirement_agent.tools.reviews import submit_review_decision

__all__ = [
    "health_check",
    "submit_requirement",
    "search_requirements",
    "submit_review_decision",
    "get_requirement_detail",
    "get_requirement_versions",
    "get_master_requirements",
]
