"""数据库 Repository 包。

从单文件 `repositories.py` 拆分为按聚合根组织的多个子模块，
此处统一 re-export，保证既有调用方 `from src.infrastructure.db.repositories import XxxRepository`
的 import 路径不变。
"""

from __future__ import annotations

from src.infrastructure.db.repositories.audit import AuditRepository
from src.infrastructure.db.repositories.chat import ChatRepository
from src.infrastructure.db.repositories.document import DocumentAssetRepository
from src.infrastructure.db.repositories.memory import MemoryRepository
from src.infrastructure.db.repositories.requirement import (
    RequirementMasterRepository,
    RequirementSourceRepository,
    RequirementVersionRepository,
)
from src.infrastructure.db.repositories.review import (
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
