"""需求生命周期的应用服务。"""

from __future__ import annotations

from src.requirement_agent.agents.analyze_agent import AnalyzeAgent
from src.requirement_agent.agents.extract_agent import ExtractAgent
from src.requirement_agent.agents.risk_agent import RiskAgent
from src.requirement_agent.common.time import as_display_iso
from src.requirement_agent.domain.requirement import RequirementSource
from src.requirement_agent.workflows.graphs import run_analysis
from src.requirement_agent.infrastructure.db.repositories import RequirementMasterRepository, RequirementSourceRepository
from src.requirement_agent.infrastructure.parser.document_parser import DocumentParser


class RequirementService:
    """需求提交与查询的应用层边界。

    职责：把一条来源需求写入 requirement_source，跑 LangGraph 分析图
    （抽取→检索→冲突/重复分析→风险→决策），并把结构化结果回填到来源元数据、
    置为待审核。HTTP 路由与内部 Tool 方法都经由本服务，不直接操作领域逻辑。
    """

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
        """提交并分析一条需求来源。

        幂等：requirement_source 以 idempotency_key 唯一；若该来源已处理过
        （processing_status 非 received/failed），直接返回现状，不重复分析。

        流程：保存来源 → 清洗/分段规整文本 → 跑 LangGraph 分析图 →
        将 extracted/analysis/risk/retrieval_filters 回填 metadata →
        置 pending_review。返回结构含 source_id、status、analysis、risk 等，
        供调用方（HTTP/内部 Tool/前端卡片）直接展示。
        """
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

        # —— Agent 编排统一走 LangGraph 分析图（抽取→检索→冲突分析→风险→决策）——
        result = run_analysis(
            source_id=saved_source.id,
            source_text=standardized_text,
            source_type=saved_source.source_type,
            requester_name=saved_source.requester_name,
        )
        extracted = dict(result.get("extracted") or {})
        analysis = dict(result.get("analysis") or {})
        risk = dict(result.get("risk") or {})
        candidates = result.get("candidates") or []

        normalized_fields = dict(metadata["standardized_document"].get("normalized_fields") or {})
        for key in ("department", "business_domain", "sensitivity_level"):
            if normalized_fields.get(key) and not metadata.get(key):
                metadata[key] = normalized_fields[key]
        metadata.setdefault("business_domain", extracted.get("business_domain", "general"))
        metadata["analysis"] = analysis
        metadata["risk"] = risk
        metadata["extracted"] = extracted
        metadata["retrieval_filters"] = {
            "channel": saved_source.source_type,
            "department": metadata.get("department"),
            "business_domain": metadata.get("business_domain"),
            "sensitivity_level": metadata.get("sensitivity_level"),
            "submitted_at": as_display_iso(saved_source.submitted_at),
        }
        self.source_repo.update_status(saved_source.id or 0, "pending_review", metadata=metadata)
        return {
            "source_id": saved_source.id,
            "idempotency_key": saved_source.idempotency_key,
            "source_type": saved_source.source_type,
            "status": "pending_review",
            "analysis": analysis,
            "risk": risk,
            "candidates": candidates,
            "next_action": result.get("decision") or result.get("next_action"),
        }

    def list_requirements(self) -> list[dict[str, object]]:
        """返回主需求列表（含领域/当前版本/功能行数/来源人/最近提交时间）。

        供“需求库”表格视图使用；按 master 表带来源上下文聚合，最多 100 条。
        """
        rows = self.master_repo.list_with_source_context(limit=100)
        return [
            {
                "requirement_key": item["requirement_key"],
                "requirement_name": item["requirement_name"],
                "final_requirement": item["final_requirement"],
                "status": item["status"],
                "business_domain": item["business_domains"][0] if item.get("business_domains") else "general",
                "current_version": item.get("current_version", 0),
                "feature_count": item.get("feature_count", 0),
                "requester_names": item.get("requester_names", []),
                "latest_source_submitted_at": item.get("latest_source_submitted_at"),
            }
            for item in rows
        ]
