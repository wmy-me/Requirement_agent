"""数据库 Repository 包。

从单文件 `repositories.py` 拆分为按聚合根组织的多个子模块，
此处统一 re-export，保证既有调用方 `from requirement_agent.infrastructure.db.repositories import XxxRepository`
的 import 路径不变。
"""

from __future__ import annotations

from requirement_agent.infrastructure.db.repositories.audit import AuditRepository
from requirement_agent.infrastructure.db.repositories.capability import (
    CapabilityRepository,
    ConstraintVocabRepository,
    FeatureCapabilityRepository,
)
from requirement_agent.infrastructure.db.repositories.chat import ChatRepository
from requirement_agent.infrastructure.db.repositories.document import DocumentAssetRepository
from requirement_agent.infrastructure.db.repositories.memory import MemoryRepository
from requirement_agent.infrastructure.db.repositories.requirement import (
    ConcurrentModificationError,
    RequirementMasterRepository,
    RequirementSourceRepository,
    RequirementVersionRepository,
)
from requirement_agent.infrastructure.db.repositories.relation import RequirementRelationRepository
from requirement_agent.infrastructure.db.repositories.title_candidate import (
    RequirementTitleCandidateRepository,
)
from requirement_agent.infrastructure.db.repositories.review import (
    RequirementFeatureRepository,
    RequirementReviewRepository,
)

__all__ = [
    "AuditRepository",
    "CapabilityRepository",
    "ChatRepository",
    "ConcurrentModificationError",
    "ConstraintVocabRepository",
    "DocumentAssetRepository",
    "FeatureCapabilityRepository",
    "MemoryRepository",
    "RequirementFeatureRepository",
    "RequirementMasterRepository",
    "RequirementRelationRepository",
    "RequirementReviewRepository",
    "RequirementSourceRepository",
    "RequirementTitleCandidateRepository",
    "RequirementVersionRepository",
]
