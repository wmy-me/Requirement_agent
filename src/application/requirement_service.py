"""Application service for requirement lifecycle operations."""

from __future__ import annotations

from src.agents.analyze_agent import AnalyzeAgent
from src.agents.extract_agent import ExtractAgent
from src.agents.risk_agent import RiskAgent
from src.domain.requirement import RequirementMaster, RequirementSource
from src.infrastructure.db.repositories import RequirementMasterRepository, RequirementSourceRepository


class RequirementService:
    """Application use case boundary for requirement submission and query."""

    def __init__(
        self,
        source_repo: RequirementSourceRepository | None = None,
        master_repo: RequirementMasterRepository | None = None,
        extract_agent: ExtractAgent | None = None,
        analyze_agent: AnalyzeAgent | None = None,
        risk_agent: RiskAgent | None = None,
    ) -> None:
        self.source_repo = source_repo or RequirementSourceRepository()
        self.master_repo = master_repo or RequirementMasterRepository()
        self.extract_agent = extract_agent or ExtractAgent()
        self.analyze_agent = analyze_agent or AnalyzeAgent()
        self.risk_agent = risk_agent or RiskAgent()

    def submit_requirement(self, source: RequirementSource) -> dict[str, object]:
        saved_source = self.source_repo.save(source)
        extracted = self.extract_agent.extract(
            source.original_text or "",
            source_type=source.source_type,
            requester_name=source.requester_name,
        )
        analysis = self.analyze_agent.analyze(extracted, [])
        risk = self.risk_agent.assess(extracted)
        requirement = RequirementMaster(
            requirement_key="REQ-000001",
            requirement_name=extracted.requirement_title,
            final_requirement=extracted.summary,
            current_version=1,
            status="active",
            lock_version=0,
        )
        self.master_repo.save(requirement)
        return {
            "idempotency_key": saved_source.idempotency_key,
            "source_type": saved_source.source_type,
            "status": "accepted",
            "requirement_key": requirement.requirement_key,
            "analysis": analysis.model_dump(mode="python"),
            "risk": risk.model_dump(mode="python"),
        }

    def list_requirements(self) -> list[dict[str, object]]:
        return [
            {
                "requirement_key": "REQ-000001",
                "requirement_name": "用户登录",
                "final_requirement": "支持邮箱和手机号登录，并支持验证码校验。",
                "status": "active",
                "business_domain": "auth",
            }
        ]
