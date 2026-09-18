"""文档 Repository：上传 asset + 分块 + 向量搜索。

`DocumentAssetRepository` 负责需求附件元数据落库，以及把正文切块、
生成向量并写入 `document_chunk`（供 RAG 检索）。embedding 依赖保持局部导入，
避免未配置向量能力时影响主流程。
"""

from __future__ import annotations

import json
import re

from sqlalchemy import text

from requirement_agent.common.snowflake import new_id, to_sid
from requirement_agent.common.time import as_display_iso
from requirement_agent.infrastructure.db.session import SessionLocal


def _chunk_content_hash(chunk_text: str) -> str:
    """分片指纹。**与迁移 019 回填时用的公式逐字一致**：
    `encode(sha256(convert_to(btrim(chunk_text), 'UTF8')), 'hex')`。

    不一致的话，老行与新行会得到两种 hash，去重索引形同虚设。
    """
    import hashlib

    return hashlib.sha256(chunk_text.strip().encode("utf-8")).hexdigest()


class DocumentAssetRepository:
    """Persistence for uploaded artefacts and chunked document RAG."""

    def save(
        self,
        *,
        file_name: str,
        content_type: str,
        storage_uri: str,
        checksum: str,
        size_bytes: int,
        source_type: str = "web",
        original_text: str | None = None,
        extracted_text: str | None = None,
        metadata: dict[str, object] | None = None,
        source_id: int | None = None,
    ) -> dict[str, object]:
        """落库一条上传文档 asset（含校验字段），返回规范化后的 asset 行。

        **按内容校验和去重**：checksum 相同的文件视为同一份资产，直接返回既有行。

        为什么必须去重：对象存储的 key 由内容哈希生成，同一份文件重复上传会写同一个
        key（幂等），但这里照插新行的话，资产表就会有 N 条记录、N 份分片与向量，
        检索结果里同一条内容重复出现。实测出现过「表里 2 条、存储里 1 个对象」的错位。
        """
        existing = self.find_by_checksum(checksum)
        if existing is not None:
            return existing
        with SessionLocal() as session:
            # 主线身份由迁移 019 固化为 (file_name, content_type)。内容变化是新版本，
            # 不是一条与历史无关的新资产；这一段必须与后面的资产插入同一事务完成。
            stream = session.execute(
                text(
                    """
                    INSERT INTO document_stream (id, file_name, content_type, current_version_no)
                    VALUES (:id, :file_name, :content_type, 0)
                    ON CONFLICT (file_name, content_type) DO UPDATE
                        SET file_name = EXCLUDED.file_name
                    RETURNING id, current_version_no
                    """
                ),
                {"id": new_id(), "file_name": file_name, "content_type": content_type},
            ).mappings().one()
            next_version = int(stream["current_version_no"]) + 1
            if next_version > 1:
                session.execute(
                    text(
                        """UPDATE document_asset
                           SET status = 'superseded', superseded_by_version_no = :next_version
                           WHERE stream_id = :stream_id AND status = 'current'"""
                    ),
                    {"stream_id": stream["id"], "next_version": next_version},
                )
            row = session.execute(
                text(
                    """
                    INSERT INTO document_asset (
                        id, file_name, content_type, storage_uri, checksum, size_bytes,
                        source_type, source_id, original_text, extracted_text, metadata,
                        stream_id, version_no, status
                    ) VALUES (
                        :id, :file_name, :content_type, :storage_uri, :checksum, :size_bytes,
                        :source_type, :source_id, :original_text, :extracted_text, CAST(:metadata AS JSONB),
                        :stream_id, :version_no, 'current'
                    )
                    RETURNING id, file_name, content_type, storage_uri, checksum, size_bytes,
                              source_type, source_id, original_text, extracted_text, metadata,
                              stream_id, version_no, status, superseded_by_version_no, created_at
                    """
                ),
                {
                    "id": new_id(),
                    "file_name": file_name,
                    "content_type": content_type,
                    "storage_uri": storage_uri,
                    "checksum": checksum,
                    "size_bytes": size_bytes,
                    "source_type": source_type,
                    "source_id": source_id,
                    "original_text": original_text or "",
                    "extracted_text": extracted_text or "",
                    "metadata": json.dumps(metadata or {}),
                    "stream_id": stream["id"],
                    "version_no": next_version,
                },
            ).mappings().one()
            session.execute(
                text("UPDATE document_stream SET current_version_no = :version_no WHERE id = :stream_id"),
                {"stream_id": stream["id"], "version_no": next_version},
            )
            session.commit()
        return self._normalize_asset_row(row)

    def find_by_checksum(self, checksum: str) -> dict[str, object] | None:
        """按内容校验和取已入库的资产（取最早的一条）；checksum 为空时返回 None。"""
        if not checksum:
            return None
        with SessionLocal() as session:
            row = session.execute(
                text(
                    "SELECT id, file_name, content_type, storage_uri, checksum, size_bytes, "
                    "source_type, source_id, original_text, extracted_text, metadata, stream_id, "
                    "version_no, status, superseded_by_version_no, created_at "
                    "FROM document_asset WHERE checksum = :checksum ORDER BY created_at LIMIT 1"
                ),
                {"checksum": checksum},
            ).mappings().first()
        return self._normalize_asset_row(row)

    def has_chunks(self, document_id: int) -> bool:
        """该文档是否已经切分过。

        用于在命中内容去重后**不再重复入队切片**：否则同一份文件传两次会得到两份分片
        与两份向量，检索结果里同一条内容重复出现。
        """
        with SessionLocal() as session:
            count = session.execute(
                text("SELECT count(*) FROM document_chunk WHERE document_id = :id"),
                {"id": document_id},
            ).scalar()
        return bool(count)

    def list_documents(self, limit: int = 20) -> list[dict[str, object]]:
        """按创建时间倒序列出上传文档 asset，返回规范化行列表。"""
        with SessionLocal() as session:
            rows = session.execute(
                text(
                    """
                    SELECT id, file_name, content_type, storage_uri, checksum, size_bytes,
                           source_type, source_id, original_text, extracted_text, metadata, stream_id,
                           version_no, status, superseded_by_version_no, created_at
                    FROM document_asset
                    ORDER BY created_at DESC
                    LIMIT :limit
                    """
                ),
                {"limit": limit},
            ).mappings().all()
        return [self._normalize_asset_row(row) for row in rows]

    def get_document(self, document_id: int) -> dict[str, object] | None:
        """按 id 取上传文档 asset；不存在返回 None。"""
        with SessionLocal() as session:
            row = session.execute(
                text(
                    """
                    SELECT id, file_name, content_type, storage_uri, checksum, size_bytes,
                           source_type, source_id, original_text, extracted_text, metadata, stream_id,
                           version_no, status, superseded_by_version_no, created_at
                    FROM document_asset
                    WHERE id = :document_id
                    """
                ),
                {"document_id": document_id},
            ).mappings().first()
        return self._normalize_asset_row(row) if row else None

    def list_versions(self, document_id: int) -> list[dict[str, object]] | None:
        """返回文档所在主线的完整版本链，旧版本也可审计、不可被当前视图误当成现行。"""
        with SessionLocal() as session:
            stream_id = session.execute(
                text("SELECT stream_id FROM document_asset WHERE id = :document_id"),
                {"document_id": document_id},
            ).scalar()
            if stream_id is None:
                return None
            rows = session.execute(
                text(
                    """SELECT id, file_name, content_type, storage_uri, checksum, size_bytes,
                              source_type, source_id, original_text, extracted_text, metadata, stream_id,
                              version_no, status, superseded_by_version_no, created_at
                       FROM document_asset WHERE stream_id = :stream_id ORDER BY version_no DESC"""
                ), {"stream_id": stream_id},
            ).mappings().all()
        return [self._normalize_asset_row(row) for row in rows]

    @staticmethod
    def _embedding_model_name() -> str:
        """当前 embedding 模型名。走统一取值器，避免与 MODEL_ROUTES 不一致。

        局部导入：与本文件其余 embedding 依赖一样（模块 docstring 说明了理由 ——
        未配置向量能力时不该影响主流程）。
        """
        from requirement_agent.infrastructure.embedding.embedding_service import (
            current_embedding_model,
        )

        return current_embedding_model()

    def add_chunks(self, document_id: int, content: str, *, chunk_size: int = 600, overlap: int = 120) -> list[dict[str, object]]:
        """将文本切块并逐块写入 document_chunk（含向量），返回保存的分块列表。

        短文本优化：`len(content) <= chunk_size` 时整段作为唯一分块（chunk_index=1），
        跳过滑动窗口切片——语义完整、单次 embedding；长文本才走 `_chunk_text` 窗口。

        幂等：同一 document 先删除旧分块再插入，任务重跑 / 重新索引不会产生重复分块
        （同一事务内完成，异常时整体回滚，不会丢旧分块留半截新分块）。
        """
        normalized = (content or "").strip()
        if not normalized:
            return []
        chunks = [normalized] if len(normalized) <= int(chunk_size) else self._chunk_text(content, chunk_size=chunk_size, overlap=overlap)
        if not chunks:
            return []
        with SessionLocal() as session:
            session.execute(text("DELETE FROM document_chunk WHERE document_id = :document_id"), {"document_id": document_id})
            saved: list[dict[str, object]] = []
            for index, chunk in enumerate(chunks, start=1):
                embedding = self._embed_text(chunk)
                row = session.execute(
                    text(
                        """
                        INSERT INTO document_chunk
                            (id, document_id, chunk_index, chunk_text, embedding, metadata,
                             embedding_model, embedding_dimension, content_hash)
                        VALUES
                            (:id, :document_id, :chunk_index, :chunk_text, :embedding,
                             CAST(:metadata AS JSONB), :embedding_model, :embedding_dimension,
                             :content_hash)
                        RETURNING id, document_id, chunk_index, chunk_text, metadata, created_at
                        """
                    ),
                    {
                        "id": new_id(),
                        "document_id": document_id,
                        "chunk_index": index,
                        "chunk_text": chunk,
                        "embedding": embedding,
                        "metadata": json.dumps({"source": "fixed-slice"}),
                        # 来源（B3.1 §4.6）：不记的话，换 embedding 模型后
                        # 「新旧向量混在一起」没有任何地方能察觉
                        "embedding_model": self._embedding_model_name(),
                        "embedding_dimension": len(embedding),
                        # ⚠️ **必须写 content_hash**（B3.1 顺手修的既有缺陷）：
                        # 这一列是迁移 019 加的，公式 `sha256(btrim(chunk_text))`，
                        # 但它**只在 019 里回填过一次，代码里从来没有人写**。
                        # 而本方法是「先 DELETE 再 INSERT」—— 于是任何一次重新分片
                        # （上传新版本、跑重算脚本）都会把已有分片的 hash 抹成 NULL，
                        # `(document_id, content_hash)` 那个去重索引随之失效。
                        "content_hash": _chunk_content_hash(chunk),
                    },
                ).mappings().one()
                saved.append(self._normalize_chunk_row(row))
            session.commit()
        return saved

    def search_chunks(self, query: str, limit: int = 5) -> list[dict[str, object]]:
        """按向量相似度检索文档分块，返回带文档信息与相似度分数的候选列表。"""
        normalized = (query or "").strip()
        if not normalized:
            return []
        # 向量以字符串形式与 CAST(... AS vector) 绑定，兼容 SQLAlchemy text()（不支持 :x::vector 直接替换）
        embedding = "[" + ",".join(str(float(x)) for x in self._embed_text(normalized)) + "]"
        with SessionLocal() as session:
            rows = session.execute(
                text(
                    """
                    SELECT dc.id, dc.document_id, dc.chunk_index, dc.chunk_text, dc.metadata, dc.created_at,
                           da.file_name, da.storage_uri,
                           1 - (dc.embedding <=> CAST(:embedding AS vector)) AS score
                    FROM document_chunk dc
                    JOIN document_asset da ON da.id = dc.document_id
                    WHERE dc.embedding IS NOT NULL
                    ORDER BY dc.embedding <=> CAST(:embedding AS vector)
                    LIMIT :limit
                    """
                ),
                {"embedding": embedding, "limit": limit},
            ).mappings().all()
        return [self._normalize_chunk_search_row(row) for row in rows]

    def get_chunks(self, document_id: int, limit: int = 20) -> list[dict[str, object]]:
        """按文档 id 列出全部分块（chunk_index 升序），返回规范化行列表。"""
        with SessionLocal() as session:
            rows = session.execute(
                text(
                    """
                    SELECT id, document_id, chunk_index, chunk_text, metadata, created_at
                    FROM document_chunk
                    WHERE document_id = :document_id
                    ORDER BY chunk_index ASC
                    LIMIT :limit
                    """
                ),
                {"document_id": document_id, "limit": limit},
            ).mappings().all()
        return [self._normalize_chunk_row(row) for row in rows]

    def _normalize_asset_row(self, row: dict[str, object] | None) -> dict[str, object] | None:
        if row is None:
            return None
        return {
            # 雪花 ID 字符串化：`id` 会进 `/documents/{id}` 系列 URL。
            "id": to_sid(row["id"]),
            "file_name": row["file_name"],
            "content_type": row["content_type"],
            "storage_uri": row["storage_uri"],
            "checksum": row["checksum"],
            "size_bytes": int(row["size_bytes"]),
            "source_type": row["source_type"],
            "source_id": to_sid(row["source_id"]),
            "original_text": row["original_text"],
            "extracted_text": row["extracted_text"],
            "metadata": dict(row["metadata"] or {}),
            "stream_id": to_sid(row.get("stream_id")),
            "version_no": int(row["version_no"]) if row.get("version_no") is not None else None,
            "status": row.get("status"),
            "superseded_by_version_no": (
                int(row["superseded_by_version_no"])
                if row.get("superseded_by_version_no") is not None else None
            ),
            "created_at": as_display_iso(row["created_at"]),
        }

    def _normalize_chunk_row(self, row: dict[str, object] | None) -> dict[str, object] | None:
        if row is None:
            return None
        return {
            "id": to_sid(row["id"]),
            "document_id": to_sid(row["document_id"]),
            "chunk_index": int(row["chunk_index"]),
            "chunk_text": row["chunk_text"],
            "metadata": dict(row["metadata"] or {}),
            "created_at": as_display_iso(row["created_at"]),
        }

    def _normalize_chunk_search_row(self, row: dict[str, object] | None) -> dict[str, object] | None:
        if row is None:
            return None
        return {
            "id": to_sid(row["id"]),
            "document_id": to_sid(row["document_id"]),
            "chunk_index": int(row["chunk_index"]),
            "chunk_text": row["chunk_text"],
            "file_name": row["file_name"],
            "storage_uri": row["storage_uri"],
            "score": float(row["score"]),
            "metadata": dict(row["metadata"] or {}),
            "created_at": as_display_iso(row["created_at"]),
        }

    def _chunk_text(self, content: str, *, chunk_size: int = 600, overlap: int = 120) -> list[str]:
        """把文本切成带重叠的固定大小分块（供向量检索）。

        采用标准滑动窗口：每块最长 `chunk_size`，窗口每次前进 `chunk_size - overlap`
        （即相邻块重叠 `overlap` 字符），避免「逐字符偏移」产生海量冗余块。
        若某块在 65% 之后遇到空格，则回退到该空格处断句，保证块边界尽量完整。
        """
        normalized = re.sub(r"\s+", " ", (content or "").strip())
        if not normalized:
            return []
        chunks: list[str] = []
        size = max(80, int(chunk_size))
        step = max(20, int(size - max(0, int(overlap))))
        start = 0
        while start < len(normalized):
            end = min(len(normalized), start + size)
            chunk = normalized[start:end].strip()
            if not chunk:
                break
            if end < len(normalized):
                last_space = chunk.rfind(" ")
                if last_space > int(size * 0.65):
                    end = start + last_space
                    chunk = normalized[start:end].strip()
            chunks.append(chunk)
            if end >= len(normalized):
                break
            start += step
        return [chunk for chunk in chunks if chunk]

    def _embed_text(self, text: str) -> list[float]:
        from requirement_agent.infrastructure.embedding.embedding_service import (
    EmbeddingService,
    current_embedding_model,
)

        return EmbeddingService().embed(text)
