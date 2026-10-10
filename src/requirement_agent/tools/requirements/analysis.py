"""Structured, read-only adapters around the legacy requirement agents."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from requirement_agent.agents.analyze_agent import AnalyzeAgent
from requirement_agent.agents.extract_agent import ExtractAgent, ExtractedRequirement
from requirement_agent.agents.risk_agent import RiskAgent
from requirement_agent.tools.base import BaseTool, ToolInput, ToolResult
from requirement_agent.tools.registry import register


class ExtractRequirementInput(ToolInput):
    text: str = Field(min_length=1, max_length=20_000)
    source_type: str = Field(default="web", max_length=64)
    requester_name: str | None = Field(default=None, max_length=128)


@register
class ExtractRequirementTool(BaseTool):
    name = "extract_requirement"
    description = "Convert requirement material into a structured, non-persistent extraction."
    input_model = ExtractRequirementInput
    output_schema: dict[str, Any] = {"type": "object"}
    allowed_consumers = ("analysis",)

    def execute(self, params: ExtractRequirementInput) -> ToolResult:
        return ToolResult.success(ExtractAgent().extract(params.text, source_type=params.source_type, requester_name=params.requester_name).model_dump(mode="python"))


class AnalyzeRequirementRelationInput(ToolInput):
    extracted: dict[str, Any]
    historical_requirements: list[dict[str, Any]] = Field(default_factory=list, max_length=50)
    analysis_mode: str = Field(default="strict", pattern="^(strict|balanced|broad)$")


@register
class AnalyzeRequirementRelationTool(BaseTool):
    name = "analyze_requirement_relation"
    description = "Assess duplicate, relation and conflict using extracted fields and retrieval evidence."
    input_model = AnalyzeRequirementRelationInput
    output_schema: dict[str, Any] = {"type": "object"}
    allowed_consumers = ("analysis",)

    def execute(self, params: AnalyzeRequirementRelationInput) -> ToolResult:
        extracted = ExtractedRequirement.model_validate(params.extracted)
        result = AnalyzeAgent().analyze(extracted, params.historical_requirements, analysis_mode=params.analysis_mode)
        return ToolResult.success(result.model_dump(mode="python"))


class AssessRequirementRiskInput(ToolInput):
    extracted: dict[str, Any]


@register
class AssessRequirementRiskTool(BaseTool):
    name = "assess_requirement_risk"
    description = "Assess quality, change and technical risks; reports degraded/information-limited results."
    input_model = AssessRequirementRiskInput
    output_schema: dict[str, Any] = {"type": "object"}
    allowed_consumers = ("analysis",)

    def execute(self, params: AssessRequirementRiskInput) -> ToolResult:
        result = RiskAgent().assess(ExtractedRequirement.model_validate(params.extracted))
        return ToolResult.success(result.model_dump(mode="python"))
