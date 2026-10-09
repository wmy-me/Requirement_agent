"""对话动态图 checkpoint：PostgreSQL JSONB 的单一持久化格式。"""
from __future__ import annotations

import hashlib
import json
from typing import Any

VERSION = 1


def pack(state: dict[str, Any]) -> dict[str, Any]:
    # 只留后续路由需要的 JSON 数据；运行时 ctx 与单调时钟不可跨进程恢复。
    allowed = {
        "source_text", "source_type", "requester_name", "intent", "completed_steps",
        "missing_information", "candidate_evidence", "route_reason", "route_count",
        "route_history", "error_state", "extracted", "candidates", "analysis", "risk",
        "retrieval", "decision", "next_action", "route_action", "clarification_question",
        "conversation_mode", "answer",
        "invalidation_history",
    }
    payload = {key: value for key, value in state.items() if key in allowed}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return {"version": VERSION, "state": payload, "checksum": hashlib.sha256(raw.encode()).hexdigest()}


def unpack(checkpoint: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """返回 (state, error)。旧格式返回 (None, 'legacy') 供兼容路径处理。"""
    if not checkpoint or "version" not in checkpoint:
        return None, "legacy"
    if checkpoint.get("version") != VERSION or not isinstance(checkpoint.get("state"), dict):
        return None, "unsupported checkpoint version"
    state = dict(checkpoint["state"])
    raw = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    if checkpoint.get("checksum") != hashlib.sha256(raw.encode()).hexdigest():
        return None, "checkpoint checksum mismatch"
    return state, None


def invalidate_for_supplement(state: dict[str, Any], source_text: str) -> dict[str, Any]:
    """新文本改变抽取输入，清除所有依赖该输入的分析产物。

    只有未改变 source_text 的 pause/resume 可以复用步骤。澄清补充不是字段级 patch：
    当前抽取器无法证明新句子不影响标题、能力、检索 query 或风险，故必须重算。
    """
    if str(state.get("source_text") or "") == source_text:
        return dict(state)
    next_state = dict(state)
    invalidated = [
        "extracted", "candidates", "analysis", "risk", "retrieval", "candidate_evidence",
        "decision", "next_action", "route_action", "clarification_question", "missing_information", "answer",
    ]
    for key in invalidated:
        next_state.pop(key, None)
    next_state["completed_steps"] = []
    next_state["route_count"] = 0
    next_state["source_text"] = source_text
    history = list(next_state.get("invalidation_history") or [])
    history.append({"reason": "supplement_changed_source_text", "invalidated": invalidated})
    next_state["invalidation_history"] = history
    return next_state
