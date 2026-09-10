"""HTTP 层的共享运行态与领域单例。

集中定义被各资源路由复用的 service / repository / agent 单例，以及
会话内存态 `chat_sessions`，避免在每个子 router 中重复实例化，
也避免模块间循环 import（各子模块统一从本模块取依赖）。
"""

from __future__ import annotations

from src.agents.analyze_agent import AnalyzeAgent
from src.agents.extract_agent import ExtractAgent
from src.agents.risk_agent import RiskAgent
from src.application.memory_service import MemoryContextBuilder, MemoryExtractor
from src.application.requirement_service import RequirementService
from src.application.retrieval_service import RetrievalService
from src.application.review_service import ReviewService
from src.infrastructure.db.repositories import (
    AuditRepository,
    ChatRepository,
    DocumentAssetRepository,
    MemoryRepository,
    RequirementFeatureRepository,
    RequirementSourceRepository,
    RequirementVersionRepository,
)
from src.infrastructure.embedding.embedding_service import EmbeddingService
from src.infrastructure.llm.openai_provider import LLMProvider
from src.infrastructure.parser.document_parser import DocumentParser
from src.infrastructure.storage.object_store import ObjectStorage
from src.infrastructure.worker.tasks import DocumentChunkingTask
from src.config.settings import settings

# —— 领域服务 ——
requirement_service = RequirementService()
retrieval_service = RetrievalService()
review_service = ReviewService()

# —— Repository ——
source_repo = RequirementSourceRepository()
version_repo = RequirementVersionRepository()
audit_repo = AuditRepository()
document_repo = DocumentAssetRepository()
feature_repo = RequirementFeatureRepository()
chat_repo = ChatRepository()
memory_repo = MemoryRepository()

# —— 记忆 / 记忆抽取 ——
memory_context_builder = MemoryContextBuilder(memory_repo)
memory_extractor = MemoryExtractor(memory_repo)

# —— Agent / 基础设施 ——
embedding_service = EmbeddingService()
extract_agent = ExtractAgent()
analyze_agent = AnalyzeAgent()
risk_agent = RiskAgent()
document_parser = DocumentParser()
object_storage = ObjectStorage()
document_chunk_task = DocumentChunkingTask()

# —— 会话内存态（进程内兜底；持久化以数据库 agent_conversation/message 为准）——
chat_sessions: dict[str, list[dict[str, object]]] = {}


def actor_id_or_default(actor_id: str | None) -> str:
    """归一化 actor：缺省用全局 API actor。"""
    normalized = (actor_id or settings.api_actor_id or "api-user").strip()
    return normalized or "api-user"


def summarize_text(text: str) -> str:
    """会话一句话摘要：LLM 优先，失败/未配置回退首段截断。"""
    snippet = " ".join((text or "").split())
    try:
        provider = LLMProvider()
        if provider.is_configured() and snippet:
            summary = provider.generate(
                f"请用一句话（不超过 80 字）概括下面这段对话的要点：\n{snippet[:2000]}",
                system_prompt="你是会话摘要器，只输出摘要本身。",
            )
            summary = (summary or "").strip()
            if summary:
                return summary[:200]
    except Exception:
        pass
    return snippet[:160] or "（空会话）"


__all__ = [
    "actor_id_or_default",
    "analyze_agent",
    "audit_repo",
    "chat_repo",
    "chat_sessions",
    "document_chunk_task",
    "document_parser",
    "document_repo",
    "embedding_service",
    "extract_agent",
    "feature_repo",
    "memory_context_builder",
    "memory_extractor",
    "memory_repo",
    "object_storage",
    "requirement_service",
    "retrieval_service",
    "review_service",
    "risk_agent",
    "source_repo",
    "summarize_text",
    "version_repo",
]