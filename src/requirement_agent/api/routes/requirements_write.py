"""需求写入路由（列表 / 文本提交 / multipart 摄取）（子批次 3.3.5 迁移）。

来源：`src/interfaces/http/rest.py` 的 `GET /api/v1/requirements`、
`POST /api/v1/requirements/submit`、`POST /api/v1/requirements/ingest`（逐字迁移）。
旧文件通过 `include_router` 在原位置复用本 router。本 router 不设 tags，由父 router 补齐。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile, status

from requirement_agent.domain.requirement import RequirementSource
from requirement_agent.api.dependencies import (
    document_chunk_task,
    document_parser,
    document_repo,
    object_storage,
    requirement_service,
)
from requirement_agent.api.schemas import RequirementSubmitRequest, RequirementSubmitResponse

router = APIRouter()


def library_filters(
    q: str = Query(default="", max_length=500),
    channel: str | None = Query(default=None, max_length=60),
    status_: str | None = Query(default=None, alias="status", max_length=60),
    requester: str | None = Query(default=None, max_length=120),
    department: str | None = Query(default=None, max_length=120),
    business_domain: str | None = Query(default=None, max_length=120),
    sensitivity_level: str | None = Query(default=None, max_length=60),
    submitted_from: str | None = Query(default=None, max_length=40),
    submitted_to: str | None = Query(default=None, max_length=40),
    has_version_ge: int | None = Query(default=None, ge=1),
) -> dict[str, object]:
    """需求库的组合筛选参数（列表与 CSV 导出共用一份定义，避免两处漂移）。

    键名与 `RetrievalService` 的 filters 对齐（`status` 除外——它只在本查询里支持，
    参数名用别名，因为 `status` 在本模块已被 fastapi.status 占用）。
    空串与纯空白一律视为「未筛选」，与既有 `/requirements/search` 的归一化方式一致。
    """
    raw: dict[str, object] = {
        "q": q,
        "channel": channel,
        "status": status_,
        "requester": requester,
        "department": department,
        "business_domain": business_domain,
        "sensitivity_level": sensitivity_level,
        "submitted_from": submitted_from,
        "submitted_to": submitted_to,
        "has_version_ge": has_version_ge,
    }
    return {
        key: value
        for key, value in raw.items()
        if value is not None and (not isinstance(value, str) or value.strip())
    }


@router.get("/api/v1/requirements")
async def list_requirements(
    filters: dict[str, object] = Depends(library_filters),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, list[dict[str, object]]]:
    """需求列表：返回存量需求数组 {"items": [...]}，支持组合筛选。

    不带任何筛选参数时行为与加筛选前一致（仅多返回渠道/部门/密级等字段供表格使用）。
    """
    return {"items": requirement_service.list_requirements(filters=filters, limit=limit)}


@router.get("/api/v1/requirements/export")
async def export_requirements(
    filters: dict[str, object] = Depends(library_filters),
    limit: int = Query(default=500, ge=1, le=5000),
) -> Response:
    """把需求库当前视图导出为 CSV：与列表接口同一套筛选参数，导出所见即所得。

    响应带 UTF-8 BOM，Excel 双击即可正常显示中文。上限比列表放宽（列表 500、导出 5000）。
    """
    content = requirement_service.export_requirements_csv(filters=filters, limit=limit)
    return Response(
        content=content,
        media_type="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="requirements.csv"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/api/v1/requirements/submit", response_model=RequirementSubmitResponse)
async def submit_requirement(payload: RequirementSubmitRequest) -> RequirementSubmitResponse:
    """提交文本需求进入评审流程：返回提交状态与 source_id。"""
    source = RequirementSource(
        idempotency_key=(
            payload.source_type
            + ":"
            + (payload.requester_id or "anonymous")
            + ":"
            + payload.original_text
        ),
        source_type=payload.source_type,
        source_event_id=payload.source_event_id,
        requester_id=payload.requester_id,
        requester_name=payload.requester_name,
        original_text=payload.original_text,
        original_payload={"input_mode": "text"},
        metadata=payload.metadata,
    )
    response = requirement_service.submit_requirement(source)
    return RequirementSubmitResponse(
        message="Requirement submitted for review",
        source_type=payload.source_type,
        status=str(response["status"]),
        source_id=response.get("source_id"),
    )


@router.post("/api/v1/requirements/ingest", response_model=RequirementSubmitResponse)
async def ingest_requirement(
    source_type: str = Form(default="web"),
    requester_id: str | None = Form(default=None),
    requester_name: str | None = Form(default=None),
    source_event_id: str | None = Form(default=None),
    original_text: str | None = Form(default=None),
    metadata: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
) -> RequirementSubmitResponse:
    """multipart 摄取需求：支持纯文本、文件或二者混合，返回提交状态与 source_id。"""
    normalized_text = (original_text or "").strip()
    parsed = None
    stored = None
    input_mode = "text"

    if file is not None:
        payload = await file.read()
        if not payload:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="uploaded file is empty")
        parsed = document_parser.parse(file.filename or "requirement-document.txt", payload)
        stored = object_storage.upload(file.filename or "requirement-document.txt", payload)
        input_mode = "mixed" if normalized_text else "document"
    elif not normalized_text:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="either original_text or file is required")

    merged_text = normalized_text
    if parsed is not None:
        merged_text = f"{normalized_text}\n\n{parsed.raw_content}".strip() if normalized_text else parsed.raw_content

    metadata_payload: dict[str, object] = {}
    if metadata:
        try:
            parsed_metadata = json.loads(metadata)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="metadata must be valid JSON") from exc
        if not isinstance(parsed_metadata, dict):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="metadata must be a JSON object")
        metadata_payload = parsed_metadata

    original_payload: dict[str, object] = {"input_mode": input_mode}
    if file is not None and parsed is not None and stored is not None:
        original_payload["file"] = {
            "name": file.filename,
            "content_type": file.content_type,
            "object_uri": stored.uri,
            "size": stored.size,
            "checksum": stored.checksum,
            "extension": parsed.extension,
            "pages": parsed.pages,
            "normalized_fields": parsed.normalized_fields or {},
            "segments": [
                {
                    "index": segment.index,
                    "kind": segment.kind,
                    "text": segment.text,
                    "field_name": segment.field_name,
                }
                for segment in parsed.segments or []
            ],
        }

    source = RequirementSource(
        idempotency_key=source_type + ":" + (requester_id or "anonymous") + ":" + merged_text,
        source_type=source_type,
        source_event_id=(source_event_id or "").strip() or None,
        requester_id=(requester_id or "").strip() or None,
        requester_name=(requester_name or "").strip() or None,
        original_text=merged_text,
        original_payload=original_payload,
        metadata=metadata_payload,
    )
    response = requirement_service.submit_requirement(source)

    # —— 文档入库后触发分片（解析出正文即入队，同步消费一次）——
    if file is not None and stored is not None:
        doc_asset = document_repo.save(
            file_name=file.filename or "requirement-document.txt",
            content_type=file.content_type or "application/octet-stream",
            storage_uri=stored.uri,
            checksum=stored.checksum,
            size_bytes=stored.size,
            source_type=source_type,
            original_text=merged_text,
            extracted_text=parsed.content if parsed is not None else merged_text,
            metadata={
                "input_mode": input_mode,
                "normalized_fields": parsed.normalized_fields or {} if parsed is not None else {},
                "segments": [
                    {"index": seg.index, "kind": seg.kind, "text": seg.text, "field_name": seg.field_name}
                    for seg in (parsed.segments or [])
                ] if parsed is not None else [],
                "source_id": response.get("source_id"),
            },
            source_id=response.get("source_id"),
        )
        if parsed is not None and parsed.content:
            document_chunk_task.enqueue(
                document_id=int(doc_asset["id"]),
                content=parsed.content,
                chunk_size=600,
                overlap=120,
            )
            document_chunk_task.process_pending(limit=1)

    return RequirementSubmitResponse(
        message="Requirement submitted for review",
        source_type=source_type,
        status=str(response["status"]),
        source_id=response.get("source_id"),
    )
