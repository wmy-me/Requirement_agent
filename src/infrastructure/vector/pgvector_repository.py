"""pgvector repository adapter."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import psycopg
from pgvector.psycopg import register_vector

from src.config.settings import settings


class RequirementVectorRepository:
    """Uses pgvector for semantic retrieval of requirement embeddings."""

    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = dsn or settings.psycopg_dsn

    def search(
        self,
        query_vector: list[float],
        limit: int = 10,
        filters: Mapping[str, object] | None = None,
    ) -> list[dict[str, object]]:
        if not query_vector:
            return []
        where_clause, params = self._build_metadata_filter(filters)
        with psycopg.connect(self.dsn) as conn:
            register_vector(conn)
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT rm.requirement_key, rm.requirement_name AS title, rm.final_requirement AS summary,
                           rm.status,
                           COALESCE(
                               MAX(s.metadata->>'business_domain'),
                               MAX(s.metadata #>> '{{standardized_document,normalized_fields,business_domain}}'),
                               MAX(s.metadata #>> '{{extracted,business_domain}}'),
                               'general'
                           ) AS business_domain,
                           ARRAY_REMOVE(ARRAY_AGG(DISTINCT s.source_type), NULL) AS source_types,
                           ARRAY_REMOVE(ARRAY_AGG(DISTINCT COALESCE(
                               s.metadata->>'department',
                               s.metadata #>> '{{standardized_document,normalized_fields,department}}'
                           )), NULL) AS departments,
                           ARRAY_REMOVE(ARRAY_AGG(DISTINCT COALESCE(
                               s.metadata->>'sensitivity_level',
                               s.metadata #>> '{{standardized_document,normalized_fields,sensitivity_level}}'
                           )), NULL) AS sensitivity_levels,
                           1 - (re.embedding <=> %s::vector) AS score
                    FROM requirement_embedding re
                    JOIN requirement_master rm ON rm.id = re.requirement_id
                    LEFT JOIN requirement_version v ON v.requirement_id = rm.id
                    LEFT JOIN requirement_version_source vs ON vs.version_id = v.id
                    LEFT JOIN requirement_source s ON s.id = vs.source_id
                    WHERE 1 = 1 {where_clause}
                    GROUP BY rm.id, rm.requirement_key, rm.requirement_name, rm.final_requirement,
                             rm.status, re.embedding
                    ORDER BY re.embedding <=> %s::vector
                    LIMIT %s
                    """,
                    (query_vector, *params, query_vector, limit),
                )
                rows = cur.fetchall()
        if not rows:
            return []
        results: list[dict[str, object]] = []
        for key, title, summary, status, business_domain, source_types, departments, sensitivity_levels, score in rows:
            results.append({
                "requirement_key": key,
                "score": float(score),
                "match_type": "vector",
                "title": title,
                "summary": summary,
                "status": status,
                "business_domain": business_domain,
                "source_types": list(source_types or []),
                "departments": list(departments or []),
                "sensitivity_levels": list(sensitivity_levels or []),
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

    def _build_metadata_filter(self, filters: Mapping[str, object] | None) -> tuple[str, list[object]]:
        if not filters:
            return "", []

        clauses: list[str] = []
        params: list[object] = []
        channel = filters.get("channel") or filters.get("source_type")
        if channel:
            clauses.append(
                """
                AND EXISTS (
                    SELECT 1
                    FROM requirement_version v2
                    JOIN requirement_version_source vs2 ON vs2.version_id = v2.id
                    JOIN requirement_source s2 ON s2.id = vs2.source_id
                    WHERE v2.requirement_id = rm.id AND s2.source_type = %s
                )
                """
            )
            params.append(str(channel))

        metadata_fields = {
            "department": ("department", "{standardized_document,normalized_fields,department}"),
            "business_domain": ("business_domain", "{standardized_document,normalized_fields,business_domain}"),
            "sensitivity_level": ("sensitivity_level", "{standardized_document,normalized_fields,sensitivity_level}"),
        }
        for filter_key, (metadata_key, normalized_path) in metadata_fields.items():
            value = filters.get(filter_key)
            if not value:
                continue
            clauses.append(
                f"""
                AND EXISTS (
                    SELECT 1
                    FROM requirement_version v3
                    JOIN requirement_version_source vs3 ON vs3.version_id = v3.id
                    JOIN requirement_source s3 ON s3.id = vs3.source_id
                    WHERE v3.requirement_id = rm.id
                      AND (
                        s3.metadata->>%s = %s
                        OR s3.metadata #>> %s::text[] = %s
                        OR s3.metadata #>> '{{extracted,{metadata_key}}}' = %s
                      )
                )
                """
            )
            params.extend([metadata_key, str(value), normalized_path, str(value), str(value)])

        submitted_from = filters.get("submitted_from")
        if submitted_from:
            clauses.append(
                """
                AND EXISTS (
                    SELECT 1
                    FROM requirement_version v4
                    JOIN requirement_version_source vs4 ON vs4.version_id = v4.id
                    JOIN requirement_source s4 ON s4.id = vs4.source_id
                    WHERE v4.requirement_id = rm.id AND s4.submitted_at >= %s::timestamptz
                )
                """
            )
            params.append(str(submitted_from))
        submitted_to = filters.get("submitted_to")
        if submitted_to:
            clauses.append(
                """
                AND EXISTS (
                    SELECT 1
                    FROM requirement_version v5
                    JOIN requirement_version_source vs5 ON vs5.version_id = v5.id
                    JOIN requirement_source s5 ON s5.id = vs5.source_id
                    WHERE v5.requirement_id = rm.id AND s5.submitted_at <= %s::timestamptz
                )
                """
            )
            params.append(str(submitted_to))
        return " ".join(clauses), params
