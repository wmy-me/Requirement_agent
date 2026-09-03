import asyncio
import os
import hashlib
import math
import json
from typing import Any, Optional
from typing_extensions import TypedDict

from dotenv import load_dotenv
from fastmcp import FastMCP
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json
from pgvector.psycopg import register_vector

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is required. Copy .env.example to .env and set DATABASE_URL.")

mcp = FastMCP("requirement-agent-tools")
_embed_model: Any = None


class HealthCheckResult(TypedDict):
    status: str


class RequirementRecordResult(TypedDict):
    id: int
    source_channel: Optional[str]
    requester: Optional[str]
    review_status: str
    input_time: Any
    created_at: Any


class MasterRequirementResult(TypedDict):
    id: int
    requirement_key: str
    requirement_name: str
    final_requirement: str
    current_version: int
    status: str
    source_channel: Optional[str]
    requester: Optional[str]
    created_at: Any
    updated_at: Any


class RequirementWriteContextResult(TypedDict):
    requirement_id: int
    requirement_key: str
    requirement_name: str
    current_version: int
    next_version_no: int
    status: str


class RequirementVersionResult(TypedDict):
    id: int
    requirement_id: int
    version_no: int
    change_type: str
    created_at: Any


class AuditLogResult(TypedDict):
    id: int
    created_at: Any


class KnowledgeUpsertResult(TypedDict):
    id: int
    requirement_id: int
    requirement_key: str
    created_at: Any


def get_conn() -> psycopg.Connection:
    conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
    register_vector(conn)
    return conn


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(str(float(v)) for v in values) + "]"


def _get_embed_model() -> Any:
    global _embed_model
    if _embed_model is None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            _embed_model = False
            return _embed_model

        model_name = os.getenv("EMBED_MODEL_NAME", "sentence-transformers/all-MiniLM-L6-v2")
        _embed_model = SentenceTransformer(model_name)
    return _embed_model


def _embed_text_fallback(text: str, dims: int = 1536) -> list[float]:
    """Deterministic fallback embedding for environments without sentence-transformers."""
    vec = [0.0] * dims
    for token in text.split():
        digest = hashlib.sha256(token.encode("utf-8", errors="ignore")).digest()
        idx = int.from_bytes(digest[:4], "big") % dims
        sign = 1.0 if (digest[4] % 2 == 0) else -1.0
        weight = 1.0 + (digest[5] / 255.0)
        vec[idx] += sign * weight

    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def _embed_text(text: str) -> list[float]:
    model = _get_embed_model()
    if model is False:
        return _embed_text_fallback(text)

    vector = model.encode(text)
    return [float(v) for v in vector.tolist()]


def _next_requirement_key(conn: psycopg.Connection) -> str:
    sql = """
    SELECT COALESCE(MAX(CAST(SUBSTRING(requirement_key FROM 5) AS INTEGER)), 0) AS max_no
    FROM requirement_master
    WHERE requirement_key ~ '^REQ-[0-9]+$'
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        row = cur.fetchone()
    next_no = int(row["max_no"]) + 1
    return f"REQ-{next_no:03d}"


def _get_master_snapshot(conn: psycopg.Connection, requirement_id: int) -> Optional[dict[str, Any]]:
    sql = """
    SELECT id, requirement_key, requirement_name, final_requirement, current_version,
           status, source_channel, requester, created_at, updated_at
    FROM requirement_master
    WHERE id = %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (requirement_id,))
        return cur.fetchone()


@mcp.tool()
def health_check() -> HealthCheckResult:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 AS ok")
            row = cur.fetchone()
    return {"status": "ok" if row and row["ok"] == 1 else "failed"}


@mcp.tool()
def create_requirement_record(
    source_channel: Optional[str] = None,
    requester: Optional[str] = None,
    requirement_summary: str = "",
    original_requirement: str = "",
    attachment_summary: str = "",
    original_files: str = "",
    raw_payload: str = "",
    review_status: str = "approved",
) -> RequirementRecordResult:
    sql = """
    INSERT INTO requirement_record (
        source_channel, requester, requirement_summary, original_requirement,
        attachment_summary, original_files, raw_payload, review_status
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    RETURNING id, source_channel, requester, review_status, input_time, created_at
    """

    # Dify/MCP side may pass JSON in plain strings; parse defensively and fallback to empty payloads.
    try:
        original_files_obj = json.loads(original_files) if original_files else []
    except Exception:
        original_files_obj = []

    try:
        raw_payload_obj = json.loads(raw_payload) if raw_payload else {}
    except Exception:
        raw_payload_obj = {}

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    source_channel,
                    requester,
                    requirement_summary,
                    original_requirement,
                    attachment_summary,
                    Json(original_files_obj),
                    Json(raw_payload_obj),
                    review_status,
                ),
            )
            row = cur.fetchone()
        conn.commit()
    return dict(row)


@mcp.tool()
def get_requirement_records(
    limit: int = 50,
    source_channel: Optional[str] = None,
    requester: Optional[str] = None,
    review_status: Optional[str] = None,
) -> list[dict[str, Any]]:
    filters: list[str] = []
    params: list[Any] = []

    if source_channel:
        filters.append("source_channel = %s")
        params.append(source_channel)
    if requester:
        filters.append("requester = %s")
        params.append(requester)
    if review_status:
        filters.append("review_status = %s")
        params.append(review_status)

    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
    sql = f"""
    SELECT id, source_channel, requester, requirement_summary, original_requirement,
            attachment_summary, original_files, raw_payload, review_status, input_time, created_at
    FROM requirement_record
    {where_clause}
    ORDER BY created_at DESC
    LIMIT %s
    """
    params.append(limit)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
    return list(rows)


@mcp.tool()
def get_history_requirements(limit: int = 20) -> list[dict[str, Any]]:
    sql = """
    SELECT id, requirement_key, requirement_name, final_requirement, current_version, status,
           source_channel, requester, created_at, updated_at
    FROM requirement_master
    ORDER BY created_at DESC
    LIMIT %s
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (limit,))
            rows = cur.fetchall()
    return list(rows)


@mcp.tool()
def get_master_requirements(limit: int = 50, status: str = "active") -> list[dict[str, Any]]:
    sql = """
    SELECT id, requirement_key, requirement_name, final_requirement, current_version, status,
           source_channel, requester, created_at, updated_at
    FROM requirement_master
    WHERE status = %s
    ORDER BY updated_at DESC
    LIMIT %s
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (status, limit))
            rows = cur.fetchall()
    return list(rows)


@mcp.tool()
def get_requirement_versions(requirement_id: int, limit: int = 20) -> list[dict[str, Any]]:
    sql = """
    SELECT id, requirement_id, version_title, version_no, change_type,
           version_requirement, change_summary, source_record_ids, created_at
    FROM requirement_version
    WHERE requirement_id = %s
    ORDER BY version_no DESC, created_at DESC
    LIMIT %s
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (requirement_id, limit))
            rows = cur.fetchall()
    return list(rows)


@mcp.tool()
def get_requirement_detail(requirement_id: int) -> MasterRequirementResult:
    with get_conn() as conn:
        row = _get_master_snapshot(conn, requirement_id)
    if row is None:
        raise ValueError(f"requirement_id={requirement_id} not found")
    return dict(row)


@mcp.tool()
def get_requirement_write_context(requirement_id: int) -> RequirementWriteContextResult:
    detail = get_requirement_detail(requirement_id)
    current_version = int(detail["current_version"])
    return {
        "requirement_id": detail["id"],
        "requirement_key": detail["requirement_key"],
        "requirement_name": detail["requirement_name"],
        "current_version": current_version,
        "next_version_no": current_version + 1,
        "status": detail["status"],
    }


@mcp.tool()
def search_requirements(
    keyword: str = "",
    requester: Optional[str] = None,
    source_channel: Optional[str] = None,
    status: Optional[str] = None,
    functional_module: Optional[str] = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    filters: list[str] = []
    params: list[Any] = []

    if keyword:
        filters.append(
            "(m.requirement_key ILIKE %s OR m.requirement_name ILIKE %s OR m.final_requirement ILIKE %s OR COALESCE(k.summary, '') ILIKE %s)"
        )
        like_keyword = f"%{keyword}%"
        params.extend([like_keyword, like_keyword, like_keyword, like_keyword])
    if requester:
        filters.append("m.requester = %s")
        params.append(requester)
    if source_channel:
        filters.append("m.source_channel = %s")
        params.append(source_channel)
    if status:
        filters.append("m.status = %s")
        params.append(status)
    if functional_module:
        filters.append("EXISTS (SELECT 1 FROM jsonb_array_elements_text(COALESCE(k.functional_modules, '[]'::jsonb)) AS module WHERE module ILIKE %s)")
        params.append(f"%{functional_module}%")

    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
    sql = f"""
    SELECT m.id, m.requirement_key, m.requirement_name, m.final_requirement,
           m.current_version, m.status, m.source_channel, m.requester,
           k.summary, k.functional_modules, m.updated_at
    FROM requirement_master m
    LEFT JOIN requirement_knowledge k ON k.requirement_id = m.id
    {where_clause}
    ORDER BY m.updated_at DESC
    LIMIT %s
    """
    params.append(limit)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
    return list(rows)


@mcp.tool()
def search_similar_requirements(query_vector: list[float], top_k: int = 5) -> list[dict[str, Any]]:
    vector_text = _vector_literal(query_vector)
    sql = """
    SELECT id, requirement_id, requirement_key, requirement_name, summary, final_requirement,
           1 - (embedding <=> %s::vector) AS similarity,
           created_at
    FROM requirement_knowledge
    WHERE embedding IS NOT NULL
    ORDER BY embedding <=> %s::vector
    LIMIT %s
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (vector_text, vector_text, top_k))
            rows = cur.fetchall()
    return list(rows)


@mcp.tool()
def search_similar_requirements_by_text(query_text: str, top_k: int = 5) -> list[dict[str, Any]]:
    """Search similar requirements from plain text. Dify can call this directly."""
    query_vector = _embed_text(query_text)
    return search_similar_requirements(query_vector=query_vector, top_k=top_k)


@mcp.tool()
def create_master_requirement(
    requirement_name: str,
    final_requirement: str,
    source_channel: Optional[str] = None,
    requester: Optional[str] = None,
    requirement_key: Optional[str] = None,
) -> MasterRequirementResult:
    with get_conn() as conn:
        if not requirement_key:
            requirement_key = _next_requirement_key(conn)

        sql = """
        INSERT INTO requirement_master (
            requirement_key, requirement_name, final_requirement,
            current_version, status, source_channel, requester
        ) VALUES (%s, %s, %s, 1, 'active', %s, %s)
        RETURNING id, requirement_key, requirement_name, current_version, status, created_at
        """
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    requirement_key,
                    requirement_name,
                    final_requirement,
                    source_channel,
                    requester,
                ),
            )
            row = cur.fetchone()
        conn.commit()
    return dict(row)


@mcp.tool()
def create_requirement_version(
    requirement_id: int,
    version_title: str,
    version_no: int,
    change_type: str,
    version_requirement: str,
    change_summary: str = "",
    source_record_ids: str = "",
) -> RequirementVersionResult:
    sql = """
    INSERT INTO requirement_version (
        requirement_id, version_title, version_no, change_type,
        version_requirement, change_summary, source_record_ids
    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
    RETURNING id, requirement_id, version_no, change_type, created_at
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    requirement_id,
                    version_title,
                    version_no,
                    change_type,
                    version_requirement,
                    change_summary,
                    source_record_ids,
                ),
            )
            row = cur.fetchone()
        conn.commit()
    return dict(row)


@mcp.tool()
def update_master_requirement(
    requirement_id: int,
    final_requirement: str,
    current_version: int,
    status: str = "active",
) -> MasterRequirementResult:
    sql = """
    UPDATE requirement_master
    SET final_requirement = %s,
        current_version = %s,
        status = %s,
        updated_at = NOW()
    WHERE id = %s
    RETURNING id, requirement_key, requirement_name, current_version, status, updated_at
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (final_requirement, current_version, status, requirement_id))
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"requirement_id={requirement_id} not found")
        conn.commit()
    return dict(row)


@mcp.tool()
def append_audit_log(
    task_id: str,
    node_name: str,
    input_summary: str,
    output_summary: str,
    result_status: str,
    error_reason: str = "",
) -> AuditLogResult:
    sql = """
    INSERT INTO task_audit_log (
        task_id, node_name, input_summary, output_summary, result_status, error_reason
    ) VALUES (%s, %s, %s, %s, %s, %s)
    RETURNING id, created_at
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (task_id, node_name, input_summary, output_summary, result_status, error_reason),
            )
            row = cur.fetchone()
        conn.commit()
    return dict(row)


@mcp.tool()
def upsert_knowledge(
    requirement_id: int,
    requirement_key: str,
    requirement_name: str,
    summary: str,
    final_requirement: str,
    embedding: list[float],
    functional_modules: Optional[list[str]] = None,
) -> KnowledgeUpsertResult:
    vector_text = _vector_literal(embedding)
    functional_modules = functional_modules or []

    with get_conn() as conn:
        master_snapshot = _get_master_snapshot(conn, requirement_id)
        if master_snapshot:
            requirement_key = requirement_key or master_snapshot["requirement_key"]
            requirement_name = requirement_name or master_snapshot["requirement_name"]
            final_requirement = final_requirement or master_snapshot["final_requirement"]

        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM requirement_knowledge WHERE requirement_id = %s ORDER BY id DESC LIMIT 1",
                (requirement_id,),
            )
            existing = cur.fetchone()

            if existing:
                sql_update = """
                UPDATE requirement_knowledge
                SET requirement_key = %s,
                    requirement_name = %s,
                    summary = %s,
                    final_requirement = %s,
                    functional_modules = %s,
                    embedding = %s::vector,
                    created_at = NOW()
                WHERE id = %s
                RETURNING id, requirement_id, requirement_key, created_at
                """
                cur.execute(
                    sql_update,
                    (
                        requirement_key,
                        requirement_name,
                        summary,
                        final_requirement,
                        Json(functional_modules),
                        vector_text,
                        existing["id"],
                    ),
                )
                row = cur.fetchone()
            else:
                sql_insert = """
                INSERT INTO requirement_knowledge (
                    requirement_id, requirement_key, requirement_name,
                    summary, final_requirement, functional_modules, embedding
                ) VALUES (%s, %s, %s, %s, %s, %s, %s::vector)
                RETURNING id, requirement_id, requirement_key, created_at
                """
                cur.execute(
                    sql_insert,
                    (
                        requirement_id,
                        requirement_key,
                        requirement_name,
                        summary,
                        final_requirement,
                        Json(functional_modules),
                        vector_text,
                    ),
                )
                row = cur.fetchone()

        conn.commit()
    return dict(row)


@mcp.tool()
def upsert_knowledge_by_text(
    requirement_id: int,
    summary: str,
    final_requirement: str,
    requirement_key: str = "",
    requirement_name: str = "",
    functional_modules: Optional[list[str]] = None,
) -> KnowledgeUpsertResult:
    """Upsert requirement knowledge by text and auto-generate embedding."""
    embedding = _embed_text(summary + "\n" + final_requirement)
    return upsert_knowledge(
        requirement_id=requirement_id,
        requirement_key=requirement_key,
        requirement_name=requirement_name,
        summary=summary,
        final_requirement=final_requirement,
        embedding=embedding,
        functional_modules=functional_modules,
    )


if __name__ == "__main__":
    transport = os.getenv("MCP_TRANSPORT", "stdio").lower()

    try:
        if transport == "streamable-http":
            host = os.getenv("MCP_HOST", "0.0.0.0")
            port = int(os.getenv("MCP_PORT", "8000"))
            mcp.run(transport="streamable-http", host=host, port=port)
        elif transport == "sse":
            host = os.getenv("MCP_HOST", "0.0.0.0")
            port = int(os.getenv("MCP_PORT", "8000"))
            mcp.run(transport="sse", host=host, port=port)
        else:
            mcp.run(transport="stdio")
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
