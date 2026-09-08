"""Application use cases shared by HTTP and MCP interfaces.

注意：本模块当前无生产引用，且 import 的 `Requirement` 在 domain 中并不存在
（领域对象为 RequirementSource / RequirementMaster 等）——属于早期遗留，
如启用请先修正模型引用；实际流程请走 RequirementService / ReviewService。
"""

from __future__ import annotations

from src.domain.requirement import Requirement, RequirementSource


class RequirementUseCases:
    """Thin orchestration layer for requirements processing.

    The actual business logic should be implemented with repositories and
    domain services, but the interface remains consistent for API and MCP.
    """

    def create_requirement(self, source: RequirementSource) -> Requirement:
        """（遗留示例）把来源需求转成一条占位 Requirement，不落库、无审核。"""
        requirement = Requirement(
            requirement_key="REQ-000001",
            requirement_name=source.requester_name or "New requirement",
            final_requirement=source.original_text or "",
        )
        return requirement
