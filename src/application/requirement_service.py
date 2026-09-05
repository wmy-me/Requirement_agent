"""需求生命周期的应用服务。"""

from __future__ import annotations

from src.agents.analyze_agent import AnalyzeAgent
from src.agents.extract_agent import ExtractAgent
from src.agents.risk_agent import RiskAgent
from src.domain.requirement import RequirementSource
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
        if saved_source.processing_status not in {"received", "failed"}:
            return {
                "source_id": saved_source.id,
                "idempotency_key": saved_source.idempotency_key,
                "source_type": saved_source.source_type,
                "status": saved_source.processing_status,
            }

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
        metadata = dict(saved_source.metadata)
        metadata["analysis"] = analysis.model_dump(mode="python")
        metadata["risk"] = risk.model_dump(mode="python")
        metadata["extracted"] = extracted.model_dump(mode="python")
        self.source_repo.update_status(saved_source.id or 0, "pending_review", metadata=metadata)
        return {
            "source_id": saved_source.id,
            "idempotency_key": saved_source.idempotency_key,
            "source_type": saved_source.source_type,
            "status": "pending_review",
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
