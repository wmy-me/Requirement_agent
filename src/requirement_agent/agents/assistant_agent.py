"""A single bounded ReAct assistant using structured tool calls."""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from requirement_agent.infrastructure.llm.model_registry import ModelRegistry
from requirement_agent.infrastructure.llm.openai_provider import ChatCompletion, LLMProvider, ToolCall
from requirement_agent.tools.invoker import invoke, tool_call_record
from requirement_agent.tools.registry import llm_schemas

_SYSTEM = """你是需求库助手。只依据本轮已返回的工具结果回答事实性问题。
需要查询时使用结构化工具调用；不得在文本中编造需求编号、来源或工具执行结果。
正式创建、修改、合并、删除、回退和批准均不是工具能力：只可协助形成草稿，不能执行。
最终回答使用简洁中文。若工具失败，明确说明失败而不是说“未找到”。"""


class AssistantModel(Protocol):
    def complete_with_tools(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ChatCompletion: ...


@dataclass(frozen=True, slots=True)
class Evidence:
    evidence_id: str
    source_type: str
    source_ref: str
    title: str
    excerpt: str
    content_hash: str

    def as_dict(self) -> dict[str, str]:
        return {
            "evidence_id": self.evidence_id,
            "source_type": self.source_type,
            "source_ref": self.source_ref,
            "title": self.title,
            "excerpt": self.excerpt,
            "content_hash": self.content_hash,
        }


@dataclass(slots=True)
class AssistantResult:
    content: str
    status: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    incomplete_reason: str | None = None

    def sources(self) -> list[dict[str, str]]:
        return [item.as_dict() for item in self.evidence]


class RequirementAssistantAgent:
    """Bounded tool-calling loop; all tools remain read-only registry tools."""

    def __init__(
        self,
        model: AssistantModel | None = None,
        *,
        max_model_turns: int = 8,
        max_tool_calls: int = 12,
        deadline_seconds: float = 120.0,
    ) -> None:
        self.model = model or LLMProvider(
            spec=ModelRegistry().get_chat_model("assistant"), task_type="assistant"
        )
        self.max_model_turns = max_model_turns
        self.max_tool_calls = max_tool_calls
        self.deadline_seconds = deadline_seconds

    def answer(
        self,
        question: str,
        *,
        allow_external: bool = False,
        history: list[dict[str, object]] | None = None,
        should_stop: Callable[[], str | None] | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> AssistantResult:
        """Execute at most the configured model/tool budget and return public output."""
        available_tools = [
            schema for schema in llm_schemas("assistant")
            if allow_external or schema["name"] not in {"web_search", "read_web_page"}
        ]
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _SYSTEM},
        ]
        # History is conversation-scoped by the repository caller.  Keep only
        # normal chat messages and a small tail: artifacts/tool payloads may
        # contain stale, unauthorized or very large data.
        for item in (history or [])[-8:]:
            role, content = item.get("role"), item.get("content")
            if role in {"user", "assistant"} and isinstance(content, str) and content.strip():
                messages.append({"role": role, "content": content[:2_000]})
        messages.append({"role": "user", "content": question})
        started = time.monotonic()
        calls: list[dict[str, Any]] = []
        evidence: list[Evidence] = []
        events: list[dict[str, Any]] = []
        tool_count = 0
        for _turn in range(self.max_model_turns):
            stopped = should_stop() if should_stop else None
            if stopped:
                return AssistantResult("本次查询已暂停或取消。", stopped, calls, evidence, events, stopped)
            if time.monotonic() - started > self.deadline_seconds:
                return AssistantResult(
                    "本次查询达到服务端时限，结果不完整。",
                    "incomplete",
                    calls,
                    evidence,
                    events,
                    "deadline_exceeded",
                )
            try:
                completion = self.model.complete_with_tools(messages, available_tools)
            except RuntimeError as exc:
                return AssistantResult(
                    f"问答模型不可用：{exc}",
                    "failed",
                    calls,
                    evidence,
                    events,
                    "model_unavailable",
                )
            # A synchronous provider cannot be force-killed safely, but its
            # elapsed time and a concurrent cancellation are checked before
            # accepting its response or starting another tool call.
            stopped = should_stop() if should_stop else None
            if stopped:
                return AssistantResult("本次查询已暂停或取消。", stopped, calls, evidence, events, stopped)
            if time.monotonic() - started > self.deadline_seconds:
                return AssistantResult("本次查询达到服务端时限，结果不完整。", "incomplete", calls, evidence, events, "deadline_exceeded")
            if not completion.tool_calls:
                content = completion.content.strip()
                if not content:
                    return AssistantResult("模型未返回可展示的回答。", "failed", calls, evidence, events, "empty_model_response")
                invalid_citations = self._invalid_citations(content, evidence)
                if invalid_citations:
                    return AssistantResult(
                        "回答包含未在本轮工具结果中验证的引用，结果不完整。",
                        "incomplete", calls, evidence, events, "invalid_citation:" + ",".join(invalid_citations),
                    )
                return AssistantResult(content, "completed", calls, evidence, events)
            messages.append(self._assistant_message(completion))
            for call in completion.tool_calls:
                stopped = should_stop() if should_stop else None
                if stopped:
                    return AssistantResult("本次查询已暂停或取消。", stopped, calls, evidence, events, stopped)
                if time.monotonic() - started > self.deadline_seconds:
                    return AssistantResult("本次查询达到服务端时限，结果不完整。", "incomplete", calls, evidence, events, "deadline_exceeded")
                tool_count += 1
                if tool_count > self.max_tool_calls:
                    return AssistantResult(
                        "本次查询达到工具调用上限，结果不完整。",
                        "incomplete",
                        calls,
                        evidence,
                        events,
                        "tool_budget_exceeded",
                    )
                started_event = {"event_type": "tool_started", "node": "assistant", "payload": {"tool": call.name, "tool_call_id": call.id}}
                events.append(started_event)
                if on_event:
                    on_event(started_event)
                result, raw_params = self._invoke(call, allow_external)
                record = tool_call_record(call.name, raw_params, result)
                record["tool_call_id"] = call.id
                calls.append(record)
                completed_event = {"event_type": "tool_completed", "node": "assistant", "payload": {"tool": call.name, "tool_call_id": call.id, "status": result.status.value}}
                events.append(completed_event)
                if on_event:
                    on_event(completed_event)
                evidence.extend(self._evidence_for(call.name, result.result, len(evidence)))
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(
                            {"status": result.status.value, "result": result.result, "message": result.message},
                            ensure_ascii=False,
                            default=str,
                        ),
                    }
                )
        return AssistantResult(
            "本次查询达到模型轮次上限，结果不完整。",
            "incomplete",
            calls,
            evidence,
            events,
            "model_turn_budget_exceeded",
        )

    @staticmethod
    def _assistant_message(completion: ChatCompletion) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": completion.content or None,
            "tool_calls": [
                {"id": call.id, "type": "function", "function": {"name": call.name, "arguments": call.arguments}}
                for call in completion.tool_calls
            ],
        }

    @staticmethod
    def _invoke(call: ToolCall, allow_external: bool) -> tuple[Any, dict[str, Any]]:
        try:
            raw = json.loads(call.arguments)
            if not isinstance(raw, dict):
                raise ValueError("工具参数必须是 JSON 对象")
        except (json.JSONDecodeError, ValueError) as exc:
            from requirement_agent.tools.base import ToolResult

            return ToolResult.error(f"参数不合法：{exc}"), {}
        if call.name in {"web_search", "read_web_page"} and not allow_external:
            from requirement_agent.tools.base import ToolResult

            return ToolResult.error("当前联网策略禁止外部工具调用"), raw
        return invoke(call.name, raw, consumer="assistant"), raw

    @staticmethod
    def _evidence_for(name: str, result: object, offset: int) -> list[Evidence]:
        """Create stable per-run evidence identifiers from returned, authorized tool data."""
        # Paginated internal tools conventionally return {items, total, ...};
        # evidence must be emitted for the actual rows rather than the envelope.
        rows = result.get("items", []) if isinstance(result, dict) and isinstance(result.get("items"), list) else (result if isinstance(result, list) else [result])
        evidence: list[Evidence] = []
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            ref = str(row.get("requirement_key") or row.get("url") or row.get("source_id") or "")
            if not ref:
                continue
            title = str(row.get("requirement_name") or row.get("title") or ref)
            excerpt = str(row.get("final_requirement") or row.get("content") or row.get("snippet") or "")[:500]
            digest = hashlib.sha256(
                json.dumps(row, ensure_ascii=False, sort_keys=True, default=str).encode()
            ).hexdigest()
            evidence.append(
                Evidence(
                    evidence_id=f"E{offset + index + 1}",
                    source_type="requirement" if "requirement_key" in row else name,
                    source_ref=ref,
                    title=title,
                    excerpt=excerpt,
                    content_hash=digest,
                )
            )
        return evidence

    @staticmethod
    def _invalid_citations(content: str, evidence: list[Evidence]) -> list[str]:
        cited = set(re.findall(r"\[(E\d+)\]", content))
        available = {item.evidence_id for item in evidence}
        return sorted(cited - available)
