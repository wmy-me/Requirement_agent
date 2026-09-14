"""面向需求抽取的 LLM 技能。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from requirement_agent.skills.base_skill import BaseSkill
from requirement_agent.skills.prompts import EXTRACT_SYSTEM_PROMPT, build_extract_user_prompt

if TYPE_CHECKING:
    from requirement_agent.agents.extract_agent import ExtractedRequirement


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
                "tags": payload.get("tags") or fallback.tags,
                "requirements": payload.get("requirements") or fallback.requirements,
                "raw_text": payload.get("raw_text") or raw_text,
            })
            return result
        except Exception:
            # JSON 无法解析、字段不合法、模型超时等场景都不阻断主流程。
            return fallback
