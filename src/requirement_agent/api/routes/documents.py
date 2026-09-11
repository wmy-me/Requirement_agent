"""文档（documents）HTTP 路由（子批次 3.3.5 迁移）。

来源：`src/interfaces/http/rest.py` 的 5 条文档路由（list / search / {id} / {id}/chunks / {id}/reindex），
逐字迁移。旧文件通过 `include_router` 在原位置复用本 router。本 router 不设 tags，由父 router 补齐。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from src.interfaces.http._state import document_chunk_task, document_repo

router = APIRouter()


@router.get("/api/v1/documents")
async def list_documents(limit: int = Query(default=20, ge=1, le=50)) -> dict[str, object]:
    """文档资产列表：返回文档数组 {"items": [...]}。"""
    return {"items": document_repo.list_documents(limit=limit)}


@router.get("/api/v1/documents/search")
async def search_document_chunks(
    q: str = Query(min_length=1, max_length=200),
    limit: int = Query(default=5, ge=1, le=20),
) -> dict[str, object]:
    """按向量相似度检索文档分块：返回 {"items": [...]}。

    必须声明在 `/documents/{document_id}` 之前——否则 "search" 会被当作
    document_id 走 422（FastAPI 按声明顺序匹配路径参数路由）。
    """
    return {"items": document_repo.search_chunks(q.strip(), limit=limit)}


@router.get("/api/v1/documents/{document_id}")
async def get_document(document_id: int) -> dict[str, object]:
    """文档详情：按 id 返回单篇文档；不存在返回 404。"""
    document = document_repo.get_document(document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
    return document


@router.get("/api/v1/documents/{document_id}/chunks")
async def get_document_chunks(
    document_id: int,
    limit: int = Query(default=20, ge=1, le=50),
) -> dict[str, object]:
    """文档分块列表：按 id 返回分块数组 {"document_id", "items": [...]}。"""
    return {"document_id": document_id, "items": document_repo.get_chunks(document_id, limit=limit)}


@router.post("/api/v1/documents/{document_id}/reindex")
async def reindex_document_chunks(document_id: int) -> dict[str, object]:
    """重新切分并索引文档正文：返回 {"status": "queued", "document_id", ...}。"""
    document = document_repo.get_document(document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
    text = (document.get("extracted_text") or document.get("original_text") or "").strip()
    if not text:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="document text is empty")
    result = document_chunk_task.enqueue(document_id=document_id, content=text, chunk_size=600, overlap=120)
    return {"status": "queued", "result": result, "document_id": document_id}
