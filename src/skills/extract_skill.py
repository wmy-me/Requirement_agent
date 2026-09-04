"""面向需求抽取的 LLM 技能。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.skills.base_skill import BaseSkill

if TYPE_CHECKING:
    from src.agents.extract_agent import ExtractedRequirement


class ExtractSkill(BaseSkill):
    """将原始需求文本抽取为结构化需求对象的技能。"""

    def extract(
        self,
        raw_text: str,
        *,
        source_type: str = "web",
        requester_name: str | None = None,
    ) -> ExtractedRequirement:
        from src.agents.extract_agent import ExtractAgent, ExtractedRequirement

        fallback = ExtractAgent._fallback_extract(raw_text, source_type=source_type, requester_name=requester_name)
        if not self.provider.is_configured():
            return fallback

        system_prompt = (
            "你是一名资深业务需求分析师。请从原始需求文本中抽取结构化业务需求，"
            "返回严格的 JSON 对象，且仅返回 JSON，不要包含 markdown 代码块。"
        )
        prompt = (
            "请从下面的需求来源文本中抽取结构化需求。\n"
            "请输出 JSON，字段说明如下：\n"
            "- requirement_title：简洁且明确的需求标题\n"
            "- summary：需求的业务摘要\n"
            "- requester_name：需求发起人或发起团队，若未知则写 null\n"
            "- source_type：需求来源渠道\n"
            "- business_domain：业务领域，例如 auth、order、report、data、workflow、general\n"
            "- priority：low、medium、high\n"
            "- tags：业务标签列表\n"
            "- requirements：子需求列表\n"
            "- raw_text：原始文本\n"
            f"source_type={source_type}\nrequester_name={requester_name or 'unknown'}\nraw_text:\n{raw_text}"
        )

        try:
            payload = self._generate_json(prompt, system_prompt)
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
            return fallback
