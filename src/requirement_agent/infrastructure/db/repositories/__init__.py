"""数据库 Repository 包。

从单文件 `repositories.py` 拆分为按聚合根组织的多个子模块，
此处统一 re-export，保证既有调用方 `from requirement_agent.infrastructure.db.repositories import XxxRepository`
的 import 路径不变。
"""

from __future__ import annotations

from requirement_agent.infrastructure.db.repositories.audit import AuditRepository
from requirement_agent.infrastructure.db.repositories.chat import ChatRepository
from requirement_agent.infrastructure.db.repositories.document import DocumentAssetRepository
from requirement_agent.infrastructure.db.repositories.memory import MemoryRepository
from requirement_agent.infrastructure.db.repositories.requirement import (
    RequirementMasterRepository,
    RequirementSourceRepository,
    RequirementVersionRepository,
)
from requirement_agent.infrastructure.db.repositories.review import (
    RequirementFeatureRepository,
    RequirementReviewRepository,
)

__all__ = [
    "AuditRepository",
    "ChatRepository",
    "DocumentAssetRepository",
    "MemoryRepository",
    "RequirementFeatureRepository",
    "RequirementMasterRepository",
    "RequirementReviewRepository",
    "RequirementSourceRepository",
    "RequirementVersionRepository",
]
