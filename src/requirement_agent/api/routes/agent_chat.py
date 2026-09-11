"""对话域：Agent 聊天（纯文本/多文件）流式生成器与端点。

归属路由前缀：`/api/v1/agent/*`（chat / chat/stream / chat/stream-with-files / run / runs/*）。
流式生成器（SSE、narrative、共享分析管线）与聊天专属辅助都收口于此，
与 `routes.py`（其余 REST）解耦。共享单例/会话态统一取自 `_state.py`。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from queue import Queue
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, File, Form, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from requirement_agent.application.decision_rules import next_action_for as decision_next_action
from requirement_agent.application.decision_rules import review_required as decision_review_required
from requirement_agent.infrastructure.llm.openai_provider import LLMProvider
from requirement_agent.api.dependencies import (
    actor_id_or_default,
    analyze_agent,
    chat_repo,
    chat_sessions,
    document_chunk_task,
    document_parser,
    document_repo,
    extract_agent,
    memory_context_builder,
    object_storage,
    retrieval_service,
    risk_agent,
)
from requirement_agent.api.schemas import AgentChatRequest, AgentRunRequest

router = APIRouter(tags=["agent"])

RISK_KEYS = (
    ("质量", "quality_risk"),
    ("变更", "change_risk"),
    ("技术影响", "technical_impact_risk"),
)

# 单个文档送入分析管线的正文上限（字符），防止长文档超 token / 拖慢响应
MAX_FILE_CHARS = 6000

NARRATIVE_SYSTEM_PROMPT = (
    "你是需求治理助手，正在与业务方对话。用户刚描述了一条需求，系统已完成结构化抽取、相似度检索、冲突与重复分析、风险评估，"
    "并给出了是否需要人工审核的判断（next_action：manual_review 或 can_commit）。"
    "请用第一人称、自然、清晰的中文，给出一段“最终结论”："
    "1) 先点明这条需求主要涉及哪个领域/方向；"
    "2) 结合检索/分析结果说明它与系统已有需求的关系：若命中相似项请直接点名 REQ 编号（如 REQ-000001）与判断（高度相似/关联/冲突）；没有则说明暂未发现重复；"
    "3) 给出“综合分析结果”要点（可用“- ”短列表）：重复/关联/冲突结论、需要澄清的点、风险评估结论；"
    "4) 结尾给出建议：manual_review 时建议补充澄清并进入人工评审，can_commit 时可作为独立需求继续处理。"
    "要求：总字数不超过 300 字；不要使用 markdown 标题、代码块或 JSON；不要罗列字段名；不要以反问句结尾。"
)


def _sse(name: str, payload: object) -> str:
    """构造一行安全的 SSE 帧（payload 以 JSON 编码，避免原始换行破坏协议）。"""
    return f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _fallback_narrative(pipeline: dict[str, object]) -> str:
    """无 LLM 或流式失败时的确定性中文总结，字段与前端卡片一致。"""
    extracted = pipeline.get("extracted") or {}
    analysis = pipeline.get("analysis") or {}
    risk = pipeline.get("risk") or {}
    candidates = analysis.get("candidates") or []

    title = extracted.get("requirement_title") or "这条需求"
    parts: list[str] = [f"我已经把你的需求理解成：「{title}」。"]
    summary = extracted.get("summary")
    if summary:
        parts.append(f"{summary}。")

    def _similarity(item: dict[str, object]) -> float:
        try:
            return float(item.get("similarity") or 0)
        except (TypeError, ValueError):
            return 0.0

    duplicates = [c for c in candidates if _similarity(c) >= 0.7]
    related = [c for c in candidates if 0.45 <= _similarity(c) < 0.7]
    if analysis.get("duplicate") and duplicates:
        keys = "、".join(str(c.get("requirement_key") or "") for c in duplicates[:3])
        parts.append(f"⚠️ 我发现 {len(duplicates)} 条高度相似的存量需求（{keys}），建议先核对是否重复。")
    elif analysis.get("related") and related:
        parts.append(f"我注意到 {len(related)} 条相关联需求，可能需要一起评估依赖关系。")
    elif candidates:
        parts.append("检索到了少量弱相关候选，但相似度不足以直接判断为相关或重复，建议人工确认。")
    if analysis.get("conflict"):
        parts.append("⚠️ 与现有权限或状态逻辑可能存在冲突，建议谨慎评审。")

    high = [label for label, key in RISK_KEYS if risk.get(key) == "high"]
    medium = [label for label, key in RISK_KEYS if risk.get(key) == "medium"]
    if high:
        parts.append(f"风险偏高：{'、'.join(high)}方向需要重点把关。")
    elif medium:
        parts.append(f"有中等风险项（{'、'.join(medium)}），建议评审时确认。")

    if not (analysis.get("duplicate") or analysis.get("conflict") or high):
        parts.append("整体没有发现明显的重复或高风险，可以先进入待办审核。")
    parts.append("需要我把这条正式提交进待办审核队列吗？")
    return "".join(parts)


def _narrative_prompt(pipeline: dict[str, object], memory_context: str | None = None) -> str:
    sections = {
        "抽取结果": pipeline.get("extracted"),
        "检索到的相似需求候选": pipeline.get("candidates"),
        "冲突/重复分析": pipeline.get("analysis"),
        "风险评估": pipeline.get("risk"),
    }
    blocks = [f"【{name}】\n{json.dumps(value, ensure_ascii=False, indent=2)}" for name, value in sections.items()]
    prompt = "请根据下面的结构化分析结果，给需求方一段口语化的总结。\n\n" + "\n\n".join(blocks)
    if memory_context:
        prompt += "\n\n【跨会话长期记忆，仅用于语气/上下文参考，不要逐字复述】\n" + memory_context
    return prompt


async def _narrative_chunks(pipeline: dict[str, object], memory_context: str | None = None) -> AsyncIterator[str]:
    """将结构化结论转成自然语言流。有 LLM 则走真实流式；否则退化为分段输出。

    生产者在线程池读取 LLM 流，消费者在事件循环用 get_nowait + sleep 轮询，
    避免阻塞默认执行器线程（防止客户端断连时线程泄漏、进而拖垮其它请求）。
    """
    provider = LLMProvider()
    if not provider.is_configured():
        # 未配置 LLM 时直接给出确定性结论（逐段输出以模拟节奏）
        text = _fallback_narrative(pipeline)
        for i in range(0, len(text), 10):
            yield text[i : i + 10]
        return

    # 生产线程写入线程安全的 stdlib Queue，事件循环轮询读取，
    # 避免跨线程使用 asyncio.Queue（其唤醒语义不保证线程安全）。
    queue: Queue[tuple[str, str]] = Queue()

    def _pump() -> None:
        try:
            for chunk in provider.generate_stream(
                _narrative_prompt(pipeline, memory_context=memory_context), system_prompt=NARRATIVE_SYSTEM_PROMPT
            ):
                queue.put(("t", chunk))
            queue.put(("done", ""))
        except Exception as exc:  # pragma: no cover - depends on external LLM
            queue.put(("err", str(exc)))

    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, _pump)
    while True:
        # 非阻塞轮询：不占用共享执行器线程，避免断连时泄漏导致线程池枯竭
        while True:
            try:
                kind, value = queue.get_nowait()
            except queue.Empty:
                break
            if kind == "t":
                yield value
            elif kind == "done":
                return
            elif kind == "err":
                # 走兜底文案，避免无限转圈
                fallback = _fallback_narrative(pipeline)
                for i in range(0, len(fallback), 10):
                    yield fallback[i : i + 10]
                return
        await asyncio.sleep(0.05)


async def _chat_stream_events(payload: AgentChatRequest) -> AsyncIterator[str]:
    """以 SSE 事件驱动完整 Agent 管线：状态 → 自然语言总结 → 结构化卡片（纯文本）。"""
    actor_id = actor_id_or_default(payload.actor_id)
    session_id, client_message_id, history = await _resolve_chat_context(
        session_id=payload.session_id, client_message_id=payload.client_message_id, actor_id=actor_id
    )

    # —— 已完成 run 重放：同一 client_message_id 重试/断连不再重算，直接回放落库结果 ——
    replayed = await _try_replay(session_id, client_message_id)
    if replayed:
        async for frame in replayed:
            yield frame
        return

    history.append({"role": "user", "content": payload.message, "client_message_id": client_message_id})
    run_text = payload.requirement_text or payload.message
    async for frame in _stream_chat_pipeline(
        session_id=session_id,
        actor_id=actor_id,
        client_message_id=client_message_id,
        user_content=payload.message,
        user_meta=None,
        run_text=run_text,
        source_type=payload.source_type,
        requester_name=payload.requester_name,
        analysis_mode=payload.analysis_mode,
        history=history,
    ):
        yield frame


async def _resolve_chat_context(
    *,
    session_id: str | None,
    client_message_id: str | None,
    actor_id: str,
) -> tuple[str, str, list[dict[str, object]]]:
    """解析并确保会话存在，返回 (session_id, client_message_id, history)。"""
    resolved_session = session_id or uuid4().hex
    conversation = chat_repo.get_conversation(resolved_session, actor_id=actor_id)
    if conversation is None:
        conversation = chat_repo.create_conversation(actor_id=actor_id, title="新对话")
        resolved_session = str(conversation["id"])
    resolved_client = client_message_id or uuid4().hex
    history = chat_sessions.setdefault(resolved_session, [])
    return resolved_session, resolved_client, history


async def _try_replay(session_id: str, client_message_id: str) -> AsyncIterator[str] | None:
    """若该 client_message_id 已有完成的 run，回放落库结果；否则返回 None 继续新流程。"""
    if not client_message_id:
        return None
    prior = chat_repo.get_run_by_client(session_id, client_message_id)
    if prior is None or prior.get("status") != "completed":
        return None
    assistant = chat_repo.get_assistant_message_for_run(str(prior["run_id"]))
    if assistant is None:
        return None

    async def _replay() -> AsyncIterator[str]:
        yield _sse("session", {"session_id": session_id, "run_id": str(prior["run_id"])})
        if assistant.get("artifacts"):
            yield _sse("artifacts", {"artifacts": assistant["artifacts"]})
        content = str(assistant.get("content") or "已完成分析。")
        for i in range(0, len(content), 10):
            yield _sse("narrative", {"t": content[i : i + 10]})
        yield _sse("done", {"run_id": str(prior["run_id"])})

    return _replay()


async def _stream_chat_pipeline(
    *,
    session_id: str,
    actor_id: str,
    client_message_id: str,
    user_content: str,
    user_meta: dict[str, object] | None,
    run_text: str,
    source_type: str,
    requester_name: str | None,
    analysis_mode: Literal["strict", "balanced", "broad"],
    history: list[dict[str, object]],
) -> AsyncIterator[str]:
    """共享的 Agent 分析管线（extract → retrieve → analyze → risk → narrative），
    纯文本与「文件+文字」两种入口复用。产出 SSE 事件并落库。"""
    user_message = chat_repo.upsert_user_message(
        conversation_id=session_id,
        content=user_content,
        actor_id=actor_id,
        client_message_id=client_message_id,
        meta=user_meta,
    )
    run = chat_repo.create_run(
        conversation_id=session_id,
        client_message_id=client_message_id,
        status="running",
        meta={"source_type": source_type, "requester_name": requester_name},
    )
    run_id = str(run["run_id"])

    pipeline: dict[str, object] = {
        "status": "ok",
        "steps": ["extract", "retrieve", "analyze", "risk", "review_decision"],
        "source_type": source_type,
        "requester_name": requester_name,
        "analysis_mode": analysis_mode,
    }
    narrative_parts: list[str] = []

    try:
        yield _sse("session", {"session_id": session_id, "run_id": run_id})

        yield _sse("step", {"step": "extract", "label": "正在理解你的需求…"})
        extracted = await run_in_threadpool(
            extract_agent.extract,
            run_text,
            source_type=source_type,
            requester_name=requester_name,
        )
        extracted_payload = extracted.model_dump(mode="python")
        pipeline["extracted"] = extracted_payload

        yield _sse("step", {"step": "retrieve", "label": "正在检索相似需求…"})
        candidates = await run_in_threadpool(
            retrieval_service.search,
            extracted_payload.get("summary") or run_text,
            limit=5,
        )
        pipeline["candidates"] = candidates

        yield _sse("step", {"step": "analyze", "label": "正在分析冲突与重复…"})
        analysis = await run_in_threadpool(analyze_agent.analyze, extracted, candidates)
        analysis_payload = analysis.model_dump(mode="python")
        pipeline["analysis"] = analysis_payload
        pipeline["analysis"]["candidates"] = [
            {
                **candidate,
                "evidence": candidate.get("evidence") or [],
                "score_label": "高" if float(candidate.get("similarity") or 0) >= 0.7 else "中" if float(candidate.get("similarity") or 0) >= 0.45 else "低",
            }
            for candidate in pipeline["analysis"].get("candidates") or []
        ]

        yield _sse("step", {"step": "risk", "label": "正在评估风险…"})
        risk = await run_in_threadpool(risk_agent.assess, extracted)
        risk_payload = risk.model_dump(mode="python")
        pipeline["risk"] = risk_payload

        pipeline["risk"]["confidence"] = round(float(pipeline["risk"].get("confidence") or 0.0), 2)

        pipeline["review_required"] = decision_review_required(analysis_payload, risk_payload)
        pipeline["next_action"] = decision_next_action(analysis_payload, risk_payload)

        # 跨会话长期记忆：仅当命中才注入最终结论 prompt
        memory_ctx = memory_context_builder.build_context(actor_id, run_text, limit=4)

        yield _sse("artifacts", {"artifacts": pipeline})

        yield _sse("narrative", {"start": True})
        try:
            async for token in _narrative_chunks(pipeline, memory_context=memory_ctx):
                narrative_parts.append(token)
                yield _sse("narrative", {"t": token})
        except Exception:
            pass
        if not narrative_parts:
            fallback_text = _fallback_narrative(pipeline)
            for i in range(0, len(fallback_text), 10):
                piece = fallback_text[i : i + 10]
                narrative_parts.append(piece)
                yield _sse("narrative", {"t": piece})

        assistant_content = "".join(narrative_parts)
        assistant_message: dict[str, object] = {
            "role": "assistant",
            "content": assistant_content,
            "artifacts": pipeline,
        }
        history.append(assistant_message)
        chat_repo.append_assistant_message(
            conversation_id=session_id,
            content=assistant_content,
            artifacts=pipeline,
            run_id=run_id,
        )
        chat_repo.update_run(run_id=run_id, status="completed", meta={"conversation_id": session_id, "assistant_message_id": user_message["id"]})
        yield _sse("done", {"run_id": run_id})
    except asyncio.CancelledError:
        chat_repo.update_run(run_id=run_id, status="cancelled", error="cancelled by client")
        raise
    except Exception as exc:
        error_text = f"分析遇到问题：{exc}"
        history.append({"role": "assistant", "content": error_text, "artifacts": None})
        chat_repo.update_run(run_id=run_id, status="failed", error=str(exc), meta={"conversation_id": session_id})
        yield _sse("error", {"message": str(exc)})
        yield _sse("done", {"run_id": run_id})


async def _collect_files(run_text: str, files: list[UploadFile] | None) -> tuple[str, list[dict[str, object]]]:
    """读取并解析上传文件，合并正文与文字为整个 run_text；返回 (run_text, files_meta)。

    单文件解析失败不中断整体，仅在 meta 中标记 error，由前端提示。
    """
    files_meta: list[dict[str, object]] = []
    merged = run_text
    for upload in files or []:
        meta: dict[str, object] = {
            "name": upload.filename or "文件",
            "size": 0,
            "title": "",
            "pages": 1,
        }
        try:
            payload = await upload.read()
            meta["size"] = len(payload)
            parsed = document_parser.parse(upload.filename or "requirement-document.txt", payload)
            meta["title"] = parsed.title
            meta["pages"] = parsed.pages
            # —— 持久化：对象存储 + 文档库 + 分片入队（与 /requirements/ingest 一致）——
            # 失败不中断本次分析：仅在 meta 标记 persist_error，正文仍参与分析。
            try:
                stored = object_storage.upload(upload.filename or "requirement-document.txt", payload)
                doc_asset = document_repo.save(
                    file_name=upload.filename or "requirement-document.txt",
                    content_type=upload.content_type or "application/octet-stream",
                    storage_uri=stored.uri,
                    checksum=stored.checksum,
                    size_bytes=stored.size,
                    source_type="web",
                    original_text=parsed.raw_content,
                    extracted_text=parsed.content,
                    metadata={
                        "input_mode": "chat",
                        "normalized_fields": parsed.normalized_fields or {},
                        "segments": [
                            {"index": seg.index, "kind": seg.kind, "text": seg.text, "field_name": seg.field_name}
                            for seg in (parsed.segments or [])
                        ],
                    },
                    source_id=None,
                )
                if parsed.content:
                    document_chunk_task.enqueue(
                        document_id=int(doc_asset["id"]),
                        content=parsed.content,
                        chunk_size=600,
                        overlap=120,
                    )
                    document_chunk_task.process_pending(limit=1)
                meta["document_id"] = doc_asset["id"]
                meta["object_uri"] = stored.uri
            except Exception as persist_exc:  # noqa: BLE001 - 持久化失败不拖垮本次分析
                meta["persist_error"] = str(persist_exc)
            content = (parsed.content or "")[:MAX_FILE_CHARS]
            if parsed.content and len(parsed.content) > MAX_FILE_CHARS:
                meta["truncated"] = True
            section = f"【文档：{parsed.title}】\n{content}"
            merged = f"{merged}\n\n{section}".strip() if merged else section
        except Exception as exc:  # noqa: BLE001 - 单个文件失败不拖垮整次请求
            meta["error"] = True
            meta["error_message"] = str(exc)
        files_meta.append(meta)
    return merged, files_meta


async def _chat_stream_files_events(
    *,
    message: str,
    session_id: str | None,
    client_message_id: str | None,
    requester_name: str | None,
    analysis_mode: Literal["strict", "balanced", "broad"],
    files: list[UploadFile] | None,
) -> AsyncIterator[str]:
    """「多文件 + 文字」组合流式聊天：解析文件并合并正文与文字，交给共享管线。"""
    actor_id = actor_id_or_default(None)
    resolved_session, resolved_client, history = await _resolve_chat_context(
        session_id=session_id, client_message_id=client_message_id, actor_id=actor_id
    )

    # 已完成 run 重放：同一 client_message_id 重试/断连不再重算
    replayed = await _try_replay(resolved_session, resolved_client)
    if replayed:
        async for frame in replayed:
            yield frame
        return

    run_text, files_meta = await _collect_files(message, files)
    # 纯文件提问时 message 为空，历史里展示文件摘要即可
    user_content = message or (f"📎 已上传 {len(files_meta)} 个文档" if files_meta else "")
    history.append({"role": "user", "content": user_content, "client_message_id": resolved_client})
    async for frame in _stream_chat_pipeline(
        session_id=resolved_session,
        actor_id=actor_id,
        client_message_id=resolved_client,
        user_content=user_content,
        user_meta={"files": files_meta} if files_meta else None,
        run_text=run_text,
        source_type="web",
        requester_name=requester_name,
        analysis_mode=analysis_mode,
        history=history,
    ):
        yield frame


# —— Agent 分析管线（/agent/run）：已迁移至 requirement_agent.api.routes.agent（子批次 3.3.2）——
# 在原位置 include 子 router，保持注册顺序；旧路径函数名继续可用（同一对象）。
# 本文件内的 chat_with_agent 亦复用该函数（经下方 import 解析）。
from requirement_agent.api.routes.agent import run_agent_pipeline  # noqa: E402,F401
from requirement_agent.api.routes.agent import router as _agent_run_router  # noqa: E402

router.include_router(_agent_run_router)


@router.post("/api/v1/agent/chat")
async def chat_with_agent(payload: AgentChatRequest) -> dict[str, object]:
    """非流式 Agent 对话：执行完整分析管线并落库，返回 {"session_id", "message", "history"}。"""
    actor_id = actor_id_or_default(payload.actor_id)
    session_id = payload.session_id or str(uuid4())
    conversation = chat_repo.get_conversation(session_id, actor_id=actor_id)
    if conversation is None:
        conversation = chat_repo.create_conversation(actor_id=actor_id, title="新对话")
        session_id = str(conversation["id"])

    history = chat_sessions.setdefault(session_id, [])
    history.append({"role": "user", "content": payload.message})

    run_text = payload.requirement_text or payload.message
    pipeline = await run_agent_pipeline(
        AgentRunRequest(
            source_type=payload.source_type,
            requester_name=payload.requester_name,
            original_text=run_text,
        )
    )
    extracted = pipeline["extracted"]
    analysis = pipeline["analysis"]
    risk = pipeline["risk"]
    answer = (
        f"已完成 Agent 分析：标题为「{extracted.get('requirement_title')}」，"
        f"重复={analysis.get('duplicate')}，冲突={analysis.get('conflict')}，"
        f"质量风险={risk.get('quality_risk')}，变更风险={risk.get('change_risk')}。"
    )
    assistant_message = {"role": "assistant", "content": answer, "artifacts": pipeline}
    history.append(assistant_message)

    client_message_id = payload.client_message_id or str(uuid4())
    chat_repo.upsert_user_message(
        conversation_id=session_id,
        content=payload.message,
        actor_id=actor_id,
        client_message_id=client_message_id,
    )
    run = chat_repo.create_run(conversation_id=session_id, client_message_id=client_message_id, status="completed")
    chat_repo.append_assistant_message(
        conversation_id=session_id,
        content=answer,
        artifacts=pipeline,
        run_id=str(run["run_id"]),
    )
    return {"session_id": session_id, "message": assistant_message, "history": chat_repo.get_messages(session_id)}


@router.post("/api/v1/agent/chat/stream")
async def stream_agent_chat(payload: AgentChatRequest) -> StreamingResponse:
    """流式 Agent 对话：以 SSE 事件输出状态、总结与结构化卡片。"""
    return StreamingResponse(
        _chat_stream_events(payload),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/api/v1/agent/chat/stream-with-files")
async def stream_agent_chat_with_files(
    message: str = Form(default=""),
    session_id: str | None = Form(default=None),
    client_message_id: str | None = Form(default=None),
    requester_name: str | None = Form(default=None),
    analysis_mode: str = Form(default="strict"),
    files: list[UploadFile] = File(default=[]),
) -> StreamingResponse:
    """豆包式多文件 + 文字组合聊天（multipart/form-data，SSE 响应）。

    各文件由后端解析，正文与输入文字合并后进入完整 Agent 管线。
    """
    return StreamingResponse(
        _chat_stream_files_events(
            message=message,
            session_id=session_id,
            client_message_id=client_message_id,
            requester_name=requester_name,
            analysis_mode=analysis_mode,
            files=files,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/api/v1/agent/chat/{session_id}")
async def get_agent_chat_history(session_id: str) -> dict[str, object]:
    """按会话 id 获取历史消息，返回 {"session_id", "history": [...]}。"""
    history = chat_repo.get_messages(session_id)
    return {"session_id": session_id, "history": history or chat_sessions.get(session_id, [])}


@router.get("/api/v1/agent/runs/{run_id}")
async def get_agent_run(run_id: str) -> dict[str, object]:
    """按 run_id 查询单次 Agent 运行记录；不存在返回 404。"""
    run = chat_repo.get_run(run_id)
    if run is None:
        from fastapi import HTTPException, status

        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="agent run not found")
    return run