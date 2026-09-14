"""异步任务：向量同步、文档切片与需求分析。

三类任务都遵循「outbox 入队 → 认领 → 执行 → 标记完成/失败」的可靠模式：
- `EmbeddingTask`：把需求正文转成向量并写入向量索引，支撑语义检索。
- `DocumentChunkingTask`：把文档正文切成固定大小（含重叠）的切片并向量化，支撑文档级检索。
- `RequirementAnalysisTask`：把渠道接入落库的需求推进到待审核（跑 LangGraph 分析图）。

任务本身是幂等的：失败会记入 outbox 并可按重试次数重新执行。
"""

from __future__ import annotations

from collections.abc import Callable

from requirement_agent.infrastructure.db.repositories import DocumentAssetRepository
from requirement_agent.infrastructure.embedding.embedding_service import EmbeddingService
from requirement_agent.infrastructure.vector.pgvector_repository import RequirementVectorRepository
from requirement_agent.infrastructure.worker.outbox import OutboxRepository


class EmbeddingTask:
    """将需求文本转成向量并更新检索索引的后台任务。"""

    def __init__(
        self,
        outbox_repo: OutboxRepository | None = None,
        embedding_service: EmbeddingService | None = None,
        vector_repo: RequirementVectorRepository | None = None,
    ) -> None:
        self.outbox_repo = outbox_repo or OutboxRepository()
        self.embedding_service = embedding_service or EmbeddingService()
        self.vector_repo = vector_repo or RequirementVectorRepository()

    def enqueue(self, *, requirement_id: int, requirement_key: str, content: str) -> str:
        """把一条 embedding_sync 事件写入 outbox（可同事务提交）。"""
        event = self.outbox_repo.enqueue(
            aggregate_type="requirement_master",
            aggregate_id=requirement_key,
            event_type="embedding_sync",
            payload={
                "requirement_id": requirement_id,
                "requirement_key": requirement_key,
                "content": content,
            },
        )
        return f"queued:{event.id}:{event.aggregate_id}:{event.event_type}"

    def process_pending(self, *, limit: int = 20, max_retries: int = 3) -> list[str]:
        """认领并执行待处理的 embedding 事件；返回每条的处理结果摘要。"""
        results: list[str] = []
        claim = getattr(self.outbox_repo, "claim_pending", None)
        events = claim(limit=limit, event_type="embedding_sync") if claim else self.outbox_repo.list_pending(limit)
        for event in events:
            try:
                requirement_id = int(event.payload["requirement_id"])
                content = str(event.payload.get("content") or "")
                vector = self.embedding_service.embed(content)
                self.vector_repo.upsert(requirement_id, vector, source_text=content)
                self.outbox_repo.mark_done(event)
                results.append(f"processed:{event.id}:{event.aggregate_id}")
            except Exception as exc:
                failed = self.outbox_repo.mark_failed(event, error=str(exc), max_retries=max_retries)
                results.append(f"{failed.status}:{event.id}:{event.aggregate_id}")
        return results


class DocumentChunkingTask:
    """把文档正文切成固定大小（含重叠）切片并写入文档索引的后台任务。"""

    def __init__(
        self,
        outbox_repo: OutboxRepository | None = None,
        document_repo: DocumentAssetRepository | None = None,
    ) -> None:
        self.outbox_repo = outbox_repo or OutboxRepository()
        self.document_repo = document_repo or DocumentAssetRepository()

    def enqueue(self, *, document_id: int, content: str, chunk_size: int = 600, overlap: int = 120) -> str:
        """把一条 document_chunk_sync 事件写入 outbox。"""
        event = self.outbox_repo.enqueue(
            aggregate_type="document_asset",
            aggregate_id=str(document_id),
            event_type="document_chunk_sync",
            payload={
                "document_id": document_id,
                "content": content,
                "chunk_size": chunk_size,
                "overlap": overlap,
            },
        )
        return f"queued:{event.id}:{event.aggregate_id}:{event.event_type}"

    def process_pending(self, *, limit: int = 20, max_retries: int = 3) -> list[str]:
        """认领并执行待处理的文档切片事件；返回每条的处理结果摘要。"""
        results: list[str] = []
        claim = getattr(self.outbox_repo, "claim_pending", None)
        events = claim(limit=limit, event_type="document_chunk_sync") if claim else self.outbox_repo.list_pending(limit)
        for event in events:
            try:
                document_id = int(event.payload["document_id"])
                content = str(event.payload.get("content") or "")
                chunk_size = int(event.payload.get("chunk_size") or 600)
                overlap = int(event.payload.get("overlap") or 120)
                self.document_repo.add_chunks(document_id, content, chunk_size=chunk_size, overlap=overlap)
                self.outbox_repo.mark_done(event)
                results.append(f"processed:{event.id}:{event.aggregate_id}")
            except Exception as exc:
                failed = self.outbox_repo.mark_failed(event, error=str(exc), max_retries=max_retries)
                results.append(f"{failed.status}:{event.id}:{event.aggregate_id}")
        return results


class RequirementAnalysisTask:
    """把渠道接入落库的需求推进到待审核的后台任务。

    与另两个任务不同的地方：它要调用应用层的 `RequirementService`。本仓库的分层方向是
    application → infrastructure（反向无先例），所以这里**不 import 应用层**，改为在构造时
    注入 `process_requirement` 可调用对象，由装配点（`api/app.py`、`workers/tasks.py`）提供。

    为什么需要它：渠道（如飞书）要求事件在 3 秒内被应答，而分析图要跑 4 个 LLM 步骤。
    所以渠道入口只落库 + 入队即返回，分析由本任务的消费循环补上。
    """

    def __init__(
        self,
        process_requirement: Callable[[int], dict[str, object]],
        outbox_repo: OutboxRepository | None = None,
    ) -> None:
        self.process_requirement = process_requirement
        self.outbox_repo = outbox_repo or OutboxRepository()

    def enqueue(self, *, source_id: int) -> str:
        """把一条 requirement_analysis 事件写入 outbox。"""
        event = self.outbox_repo.enqueue(
            aggregate_type="requirement_source",
            aggregate_id=str(source_id),
            event_type="requirement_analysis",
            payload={"source_id": source_id},
        )
        return f"queued:{event.id}:{event.aggregate_id}:{event.event_type}"

    def process_pending(self, *, limit: int = 20, max_retries: int = 3) -> list[str]:
        """认领并执行待处理的分析事件；返回每条的处理结果摘要。

        `process_requirement` 自身对已处理过的来源短路，所以重复消费是安全的。
        """
        results: list[str] = []
        claim = getattr(self.outbox_repo, "claim_pending", None)
        events = claim(limit=limit, event_type="requirement_analysis") if claim else self.outbox_repo.list_pending(limit)
        for event in events:
            try:
                source_id = int(event.payload["source_id"])
                self.process_requirement(source_id)
                self.outbox_repo.mark_done(event)
                results.append(f"processed:{event.id}:{event.aggregate_id}")
            except Exception as exc:
                failed = self.outbox_repo.mark_failed(event, error=str(exc), max_retries=max_retries)
                results.append(f"{failed.status}:{event.id}:{event.aggregate_id}")
        return results
