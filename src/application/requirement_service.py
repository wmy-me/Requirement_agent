"""需求生命周期的应用服务。"""

from __future__ import annotations

from src.agents.analyze_agent import AnalyzeAgent
from src.agents.extract_agent import ExtractAgent
from src.agents.risk_agent import RiskAgent
from src.domain.requirement import RequirementSource
from src.infrastructure.db.repositories import RequirementMasterRepository, RequirementSourceRepository
from src.infrastructure.parser.document_parser import DocumentParser


class RequirementService:
    """对需求提交、查询和结构化分析的应用层边界。"""

    def __init__(
        self,
        source_repo: RequirementSourceRepository | None = None,
        master_repo: RequirementMasterRepository | None = None,
        extract_agent: ExtractAgent | None = None,
        analyze_agent: AnalyzeAgent | None = None,
        risk_agent: RiskAgent | None = None,
        document_parser: DocumentParser | None = None,
    ) -> None:
        self.source_repo = source_repo or RequirementSourceRepository()
        self.master_repo = master_repo or RequirementMasterRepository()
        self.extract_agent = extract_agent or ExtractAgent()
        self.analyze_agent = analyze_agent or AnalyzeAgent()
        self.risk_agent = risk_agent or RiskAgent()
        self.document_parser = document_parser or DocumentParser()

    def submit_requirement(self, source: RequirementSource) -> dict[str, object]:
        saved_source = self.source_repo.save(source)
        if saved_source.processing_status not in {"received", "failed"}:
            return {
                "source_id": saved_source.id,
                "idempotency_key": saved_source.idempotency_key,
                "source_type": saved_source.source_type,
                "status": saved_source.processing_status,
            }

        standardized_text = self.document_parser.clean_text(saved_source.original_text or "")
        segments = self.document_parser.segment(standardized_text)
        metadata = dict(saved_source.metadata)
        metadata["standardized_document"] = {
            "content": standardized_text,
            "segments": [
                {
                    "index": segment.index,
                    "kind": segment.kind,
                    "text": segment.text,
                    "field_name": segment.field_name,
                }
                for segment in segments
            ],
            "normalized_fields": self.document_parser.normalize_fields(segments),
        }
        self.source_repo.update_extraction(
            saved_source.id or 0,
            extracted_text=standardized_text,
            metadata=metadata,
        )

        extracted = self.extract_agent.extract(
            standardized_text,
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
        normalized_fields = dict(metadata["standardized_document"].get("normalized_fields") or {})
        for key in ("department", "business_domain", "sensitivity_level"):
            if normalized_fields.get(key) and not metadata.get(key):
                metadata[key] = normalized_fields[key]
        metadata.setdefault("business_domain", extracted.business_domain)
        metadata["analysis"] = analysis.model_dump(mode="python")
        metadata["risk"] = risk.model_dump(mode="python")
        metadata["extracted"] = extracted.model_dump(mode="python")
        metadata["retrieval_filters"] = {
            "channel": saved_source.source_type,
            "department": metadata.get("department"),
            "business_domain": metadata.get("business_domain"),
            "sensitivity_level": metadata.get("sensitivity_level"),
            "submitted_at": saved_source.submitted_at.isoformat() if saved_source.submitted_at else None,
        }
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
