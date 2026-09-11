"""从来源需求生成规范化“最终需求”标题/描述的共享函数（从 ReviewService 抽取）。"""

from __future__ import annotations

from requirement_agent.domain.requirement import RequirementSource


def canonical_title(source: RequirementSource, edited_requirement: str | None) -> str:
    edited = (edited_requirement or "").strip()
    if edited:
        return edited.splitlines()[0] or "待命名需求"
    extracted = source.metadata.get("extracted") if isinstance(source.metadata, dict) else None
    if isinstance(extracted, dict):
        title = str(extracted.get("requirement_title") or "").strip()
        if title:
            return title
    return (source.extracted_text or source.original_text or "待命名需求").strip().splitlines()[0]


def canonical_requirement(source: RequirementSource, edited_requirement: str | None) -> str:
    edited = (edited_requirement or "").strip()
    if edited:
        return edited
    extracted = source.metadata.get("extracted") if isinstance(source.metadata, dict) else None
    if isinstance(extracted, dict):
        requirements = extracted.get("requirements")
        if isinstance(requirements, list) and requirements:
            normalized = [str(item).strip() for item in requirements if str(item).strip()]
            if normalized:
                return "\n".join(normalized)
        summary = str(extracted.get("summary") or "").strip()
        if summary:
            return summary
    return (source.extracted_text or source.original_text or "待补充需求说明").strip()
