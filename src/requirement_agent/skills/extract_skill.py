"""面向需求抽取的 LLM 技能。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from requirement_agent.skills.base_skill import BaseSkill
from requirement_agent.skills.prompts import EXTRACT_SYSTEM_PROMPT, build_extract_user_prompt

if TYPE_CHECKING:
    from requirement_agent.agents.extract_agent import ExtractedRequirement

logger = logging.getLogger(__name__)

# 从对象型条目里优先取这些键作为正文（按语义贴近程度排序）
_ITEM_TEXT_KEYS = (
    "text", "content", "requirement", "description", "detail", "statement",
    "summary", "name", "title", "value",
)


def _flatten_item(item: object) -> str:
    """把一条「子需求 / 标签」归一成字符串。

    模型经常把 `requirements` 输出成对象数组（实测形如 `{"id": "REQ-001", "module": "...", ...}`），
    而字段声明是 `list[str]`，直接 `model_validate` 会**整体校验失败**——一个格式偏差
    会让抽取的全部字段一起降级到启发式，代价与收益完全不成比例。
    """
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        for key in _ITEM_TEXT_KEYS:
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        # 认不出常用键就把所有值拼起来：宁可读起来啰嗦，也不能把内容整个丢掉
        parts = [str(v).strip() for v in item.values() if v not in (None, "", [], {})]
        return "｜".join(parts)
    return str(item).strip()


def _coerce_str_list(value: object) -> list[str]:
    """把模型给的任意形状归一成字符串列表；非列表一律返回空列表。"""
    if not isinstance(value, (list, tuple)):
        return []
    items = [_flatten_item(item) for item in value]
    return [item for item in items if item]


class ExtractSkill(BaseSkill):
    """LLM 抽取技能。

    Skill 只负责提示词、JSON 解析与字段归一化；
    是否调用它、以及失败后的回退策略，由 ExtractAgent 决定。
    """

    def extract(
        self,
        raw_text: str,
        *,
        source_type: str = "web",
        requester_name: str | None = None,
    ) -> ExtractedRequirement:
        """调用模型抽取结构化需求；任何异常都回退到启发式结果。"""
        from requirement_agent.agents.extract_agent import ExtractAgent, ExtractedRequirement

        fallback = ExtractAgent._fallback_extract(raw_text, source_type=source_type, requester_name=requester_name)
        if not self.provider.is_configured():
            return fallback

        system_prompt = EXTRACT_SYSTEM_PROMPT
        prompt = build_extract_user_prompt(
            source_type=source_type, requester_name=requester_name, raw_text=raw_text
        )

        try:
            payload = self._generate_json(prompt, system_prompt)
            # 只对缺失字段做兜底，不覆盖模型已经给出的有效业务字段。
            result = ExtractedRequirement.model_validate({
                "requirement_title": payload.get("requirement_title") or fallback.requirement_title,
                "summary": payload.get("summary") or fallback.summary,
                "requester_name": payload.get("requester_name") or requester_name,
                "source_type": payload.get("source_type") or source_type,
                "business_domain": payload.get("business_domain") or fallback.business_domain,
                "priority": payload.get("priority") or fallback.priority,
                # tags / requirements 一律先归一化：模型可能给出对象数组，
                # 直接交给校验会让**整个抽取**失败（见 _flatten_item 的说明）。
                "tags": _coerce_str_list(payload.get("tags")) or fallback.tags,
                "requirements": _coerce_str_list(payload.get("requirements")) or fallback.requirements,
                "raw_text": payload.get("raw_text") or raw_text,
            })
            return result
        except Exception as exc:
            # JSON 无法解析、字段不合法、模型超时等场景都不阻断主流程。
            # 但必须留痕：否则失败完全静默，计量里只剩成功侧数据。
            logger.warning("event=skill_fallback skill=extract error=%s", exc)
            return fallback
