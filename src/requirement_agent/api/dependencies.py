"""API 层的共享运行态与领域单例。

集中定义被各资源路由复用的 service / repository / agent 单例，以及
会话内存态 `chat_sessions`，避免在每个子 router 中重复实例化，
也避免模块间循环 import（各子模块统一从本模块取依赖）。
"""

from __future__ import annotations

import logging

from requirement_agent.agents.analyze_agent import AnalyzeAgent
from requirement_agent.agents.extract_agent import ExtractAgent
from requirement_agent.agents.risk_agent import RiskAgent
from requirement_agent.application.channel_service import ChannelIngestService
from requirement_agent.application.memory_service import MemoryContextBuilder, MemoryExtractor
from requirement_agent.application.requirement_service import RequirementService
from requirement_agent.application.retrieval_service import RetrievalService
from requirement_agent.application.review_service import ReviewService
from requirement_agent.infrastructure.db.repositories import (
    AuditRepository,
    CapabilityRepository,
    ChatRepository,
    ConstraintVocabRepository,
    DocumentAssetRepository,
    MemoryRepository,
    RequirementFeatureRepository,
    RequirementRelationRepository,
    RequirementSourceRepository,
    RequirementVersionRepository,
)
from requirement_agent.infrastructure.embedding.embedding_service import EmbeddingService
from requirement_agent.infrastructure.llm.openai_provider import LLMProvider
from requirement_agent.infrastructure.parser.document_parser import DocumentParser
from requirement_agent.infrastructure.storage.object_store import ObjectStorage
from requirement_agent.infrastructure.channels.feishu_client import FeishuClient
from requirement_agent.infrastructure.worker.outbox import OutboxRepository
from requirement_agent.infrastructure.worker.tasks import DocumentChunkingTask, RequirementAnalysisTask
from requirement_agent.config.settings import settings

logger = logging.getLogger(__name__)

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
relation_repo = RequirementRelationRepository()
# 能力 / 限定条件的受控词表（方案批次 1）
capability_repo = CapabilityRepository()
constraint_repo = ConstraintVocabRepository()
# 运维面（/api/v1/ops）用：查看异步队列积压与处理死信
outbox_repo = OutboxRepository()

# —— 记忆 / 记忆抽取 ——
memory_context_builder = MemoryContextBuilder(memory_repo)
memory_extractor = MemoryExtractor(memory_repo)

# —— 渠道接入 ——
# 分析任务要调用应用层服务，装配点在这里注入（见 RequirementAnalysisTask 的说明）。
feishu_client = FeishuClient(
    app_id=settings.feishu_app_id,
    app_secret=settings.feishu_app_secret.get_secret_value(),
    verification_token=settings.feishu_verification_token.get_secret_value(),
    encrypt_key=settings.feishu_encrypt_key.get_secret_value(),
)
requirement_analysis_task = RequirementAnalysisTask(requirement_service.process_requirement)
channel_ingest_service = ChannelIngestService(requirement_analysis_task)

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
    except Exception as exc:
        # 摘要失败不影响会话收尾，但必须留痕：否则失败完全静默
        logger.warning("event=summarize_fallback error=%s", exc)
    return snippet[:160] or "（空会话）"


__all__ = [
    "actor_id_or_default",
    "analyze_agent",
    "audit_repo",
    "capability_repo",
    "channel_ingest_service",
    "chat_repo",
    "chat_sessions",
    "constraint_repo",
    "document_chunk_task",
    "document_parser",
    "document_repo",
    "embedding_service",
    "extract_agent",
    "feature_repo",
    "feishu_client",
    "memory_context_builder",
    "memory_extractor",
    "memory_repo",
    "object_storage",
    "outbox_repo",
    "relation_repo",
    "requirement_analysis_task",
    "requirement_service",
    "retrieval_service",
    "review_service",
    "risk_agent",
    "source_repo",
    "summarize_text",
    "version_repo",
]
