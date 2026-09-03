"""pgvector repository adapter."""

from __future__ import annotations

from typing import Any

import psycopg
from pgvector.psycopg import register_vector

from src.config.settings import settings


class RequirementVectorRepository:
    """Uses pgvector for semantic retrieval of requirement embeddings."""

    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = dsn or settings.psycopg_dsn

    def search(self, query_vector: list[float], limit: int = 10) -> list[dict[str, object]]:
        if not query_vector:
            return []
        with psycopg.connect(self.dsn) as conn:
            register_vector(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT rm.requirement_key, rm.requirement_name AS title, rm.final_requirement AS summary,
                           rm.status,
                           1 - (re.embedding <=> %s::vector) AS score
                    FROM requirement_embedding re
                    JOIN requirement_master rm ON rm.id = re.requirement_id
                    ORDER BY re.embedding <=> %s::vector
                    LIMIT %s
                    """,
                    (query_vector, query_vector, limit),
                )
                rows = cur.fetchall()
        if not rows:
            return []
        results: list[dict[str, object]] = []
        for key, title, summary, status, score in rows:
            results.append({
                "requirement_key": key,
                "score": float(score),
                "match_type": "vector",
                "title": title,
                "summary": summary,
                "status": status,
            })
        return results

    def upsert(self, requirement_id: int, embedding: list[float], *, source_text: str) -> dict[str, object]:
        with psycopg.connect(self.dsn) as conn:
            register_vector(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO requirement_embedding (requirement_id, embedding, source_text)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (requirement_id) DO UPDATE SET
                        embedding = EXCLUDED.embedding,
                        source_text = EXCLUDED.source_text,
                        updated_at = NOW()
                    RETURNING requirement_id, source_text
                    """,
                    (requirement_id, embedding, source_text),
                )
                result = cur.fetchone()
            conn.commit()
        return {"requirement_id": result[0], "source_text": result[1]}

    def build_similarity_filter(self, rows: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
        ordered = sorted(rows, key=lambda row: float(row.get("score", 0.0)), reverse=True)
        return ordered[: max(1, min(limit, 50))]
