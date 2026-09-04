"""需求生命周期的应用服务。"""

from __future__ import annotations

from src.agents.analyze_agent import AnalyzeAgent
from src.agents.extract_agent import ExtractAgent
from src.agents.risk_agent import RiskAgent
from src.domain.requirement import RequirementMaster, RequirementSource
from src.infrastructure.db.repositories import RequirementMasterRepository, RequirementSourceRepository


class RequirementService:
    """对需求提交、查询和结构化分析的应用层边界。"""

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

        historical = []
        for item in self.master_repo.list():
            historical.append(
                {
                    "requirement_key": item.requirement_key,
                    "requirement_name": item.requirement_name,
                    "final_requirement": item.final_requirement,
                    "status": item.status,
                }
            )

        analysis = self.analyze_agent.analyze(extracted, historical)
        risk = self.risk_agent.assess(extracted)
        requirement_key = self._next_requirement_key()
        requirement = RequirementMaster(
            requirement_key=requirement_key,
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
        rows = self.master_repo.list()
        return [
            {
                "requirement_key": item.requirement_key,
                "requirement_name": item.requirement_name,
                "final_requirement": item.final_requirement,
                "status": item.status,
                "business_domain": "general",
            }
            for item in rows
        ]

    def _next_requirement_key(self) -> str:
        existing = {item.requirement_key for item in self.master_repo.list()}
        counter = 1
        while f"REQ-{counter:06d}" in existing:
            counter += 1
        return f"REQ-{counter:06d}"
