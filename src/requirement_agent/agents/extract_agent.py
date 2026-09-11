"""将原始需求文本转换为结构化模型的抽取 Agent。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from requirement_agent.skills.extract_skill import ExtractSkill


class ExtractedRequirement(BaseModel):
    """抽取阶段产出的标准需求载体。

    该对象会贯穿检索、关系分析、风险评估、审核落库与对话回放；
    `requirements` 表示按功能条目拆分后的候选行，后续版本管理直接复用它做 feature 初稿。
    """

    requirement_title: str
    summary: str
    requester_name: str | None = None
    source_type: str = "web"
    business_domain: str = "general"
    priority: Literal["low", "medium", "high"] = "medium"
    tags: list[str] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)
    raw_text: str


class ExtractAgent:
    """需求抽取入口。

    Agent 只负责“入口编排 + 降级策略”：优先调用 LLM Skill；
    未配置模型或模型返回非法 JSON 时，回退到本地启发式抽取，保证提交链路不断。
    """

    _domain_keywords: dict[str, set[str]] = {
        "auth": {"登录", "认证", "权限", "账号", "验证码", "密码"},
        "order": {"下单", "订单", "支付", "退款", "发票"},
        "report": {"报表", "统计", "看板", "查询", "导出"},
        "data": {"数据", "同步", "导入", "导出", "etl", "字段"},
        "workflow": {"审批", "流程", "任务", "状态", "通知"},
    }

    def __init__(self, skill: ExtractSkill | None = None) -> None:
        self.skill = skill or ExtractSkill()

    def extract(
        self,
        raw_text: str,
        *,
        source_type: str = "web",
        requester_name: str | None = None,
    ) -> ExtractedRequirement:
        """把原文转换成结构化需求，不写数据库。"""
        if not self.skill.provider.is_configured():
            return self._fallback_extract(raw_text, source_type=source_type, requester_name=requester_name)
        return self.skill.extract(raw_text, source_type=source_type, requester_name=requester_name)

    @staticmethod
    def _fallback_extract(
        raw_text: str,
        *,
        source_type: str = "web",
        requester_name: str | None = None,
    ) -> ExtractedRequirement:
        """本地兜底抽取：尽量给出稳定标题、摘要、领域、标签和功能行。"""
        cleaned = (raw_text or "").strip()
        if not cleaned:
            cleaned = "新需求：补充需求说明。"

        lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
        title = ExtractAgent()._extract_title(cleaned, lines)
        summary = ExtractAgent()._summarize(cleaned)
        requirements = ExtractAgent()._extract_requirements(lines)
        domain = ExtractAgent()._detect_domain(cleaned)
        tags = ExtractAgent()._extract_tags(cleaned, domain)
        priority = ExtractAgent()._detect_priority(cleaned)

        return ExtractedRequirement(
            requirement_title=title,
            summary=summary,
            requester_name=requester_name,
            source_type=source_type,
            business_domain=domain,
            priority=priority,
            tags=tags,
            requirements=requirements,
            raw_text=cleaned,
        )

    def _extract_title(self, raw_text: str, lines: list[str]) -> str:
        """优先取首行作为标题，避免摘要截断后丢失业务主语。"""
        if lines:
            first = lines[0].rstrip("：:")
            if len(first) <= 80:
                return first
        if len(raw_text) <= 80:
            return raw_text
        return raw_text[:80].rstrip()

    def _summarize(self, raw_text: str) -> str:
        sentence = raw_text.replace("\n", " ").strip()
        if len(sentence) <= 200:
            return sentence
        return sentence[:197].rstrip() + "..."

    def _extract_requirements(self, lines: list[str]) -> list[str]:
        """把来源文本按“可独立理解的一行功能”整理为 feature 候选。"""
        requirements: list[str] = []
        for line in lines:
            normalized = line.strip("-·•* ")
            if normalized and len(normalized) > 4:
                requirements.append(normalized)
        if not requirements:
            requirements.append(self._summarize(lines[0] if lines else "新需求：补充需求说明。"))
        return requirements[:8]

    def _detect_domain(self, raw_text: str) -> str:
        """按保守关键词命中领域；宁可落到 general，也不臆造业务域。"""
        lower = raw_text.lower()
        for name, keywords in self._domain_keywords.items():
            if any(keyword in lower for keyword in (k.lower() for k in keywords)):
                return name
        return "general"

    def _extract_tags(self, raw_text: str, domain: str) -> list[str]:
        """标签主要服务于相似度分析与风险规则，不追求完整本体。"""
        base = {domain}
        for token in ("登录", "审批", "报表", "支付", "通知", "导入", "导出", "权限"):
            if token in raw_text:
                base.add(token)
        return sorted(base)

    def _detect_priority(self, raw_text: str) -> Literal["low", "medium", "high"]:
        """优先级只做粗分层，供审核与风险规则参考。"""
        lowered = raw_text.lower()
        if any(keyword in lowered for keyword in ["高优先级", "urgency", "紧急", "重大", "关键", "合规"]):
            return "high"
        if any(keyword in lowered for keyword in ["可选", "nice to have", "非核心"]):
            return "low"
        return "medium"
