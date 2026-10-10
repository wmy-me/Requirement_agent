"""Input-mode aware intent classification for the single assistant.

Classification selects a safe capability set; it never grants permission to write a
requirement.  Explicit submission still goes through the existing draft/review flow.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

InputMode = Literal["auto", "ask_library", "draft_requirement", "research"]
AssistantIntent = Literal[
    "casual",
    "library_query",
    "compare_explain",
    "external_research",
    "new_requirement",
    "change_requirement",
    "operation_request",
]

_CASUAL = ("你好", "您好", "你是谁", "你能做什么", "在吗", "hi", "hello", "谢谢", "感谢")
_QUERY = ("哪些需求", "有哪些需求", "有哪些", "查看", "查询", "查一下", "是什么", "详情", "版本", "对比", "改了什么")
_RESEARCH = ("搜索网页", "联网查询", "网上查", "调研", "最新消息", "外部资料")
_DRAFT = ("整理提案", "整理成需求", "提交需求", "创建需求", "新增", "新需求")
_CHANGE = ("修改需求", "变更需求", "更新需求", "改需求")
_OPERATIONS = ("删除", "合并", "回退", "批准", "审批通过")
_REQ_KEY = re.compile(r"\bREQ-\d+\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class IntentDecision:
    intent: AssistantIntent
    mode: InputMode
    reason: str

    @property
    def creates_draft(self) -> bool:
        return self.intent in {"new_requirement", "change_requirement"}


class IntentService:
    """Classify a message without treating keyword matches as write authorization."""

    def classify(self, message: str, *, mode: InputMode = "auto") -> IntentDecision:
        text = " ".join(message.split())
        lower = text.lower()
        if mode == "ask_library":
            return IntentDecision("library_query", mode, "用户明确选择需求库问答模式。")
        if mode == "research":
            return IntentDecision("external_research", mode, "用户明确选择外部调研模式。")
        if mode == "draft_requirement":
            intent: AssistantIntent = "change_requirement" if _REQ_KEY.search(text) or any(word in text for word in _CHANGE) else "new_requirement"
            return IntentDecision(intent, mode, "用户明确选择需求草稿模式；仍需人工审批后才能正式写入。")
        # "删除功能有哪些需求" is a library query; an operation is only
        # requested when a concrete requirement identity is the target.
        if any(word in text for word in _OPERATIONS) and _REQ_KEY.search(text):
            return IntentDecision("operation_request", mode, "针对具体需求的正式操作不会作为模型工具执行。")
        if any(word in text for word in _RESEARCH):
            return IntentDecision("external_research", mode, "用户要求外部调研。")
        if any(word in text for word in _CHANGE):
            return IntentDecision("change_requirement", mode, "消息明确要求修改已有需求。")
        if any(word in text for word in _DRAFT) and not any(word in text for word in _QUERY):
            return IntentDecision("new_requirement", mode, "消息明确要求整理或提交新的需求提案。")
        if any(word in lower for word in _CASUAL) and len(text) <= 40:
            return IntentDecision("casual", mode, "短消息是普通交流。")
        if any(word in text for word in _QUERY) or _REQ_KEY.search(text):
            intent = "compare_explain" if ("对比" in text or "版本" in text or "改了什么" in text) else "library_query"
            return IntentDecision(intent, mode, "消息是对现有需求库的查询或解释。")
        return IntentDecision("library_query", mode, "auto 模式默认先执行无副作用的需求库问答。")
