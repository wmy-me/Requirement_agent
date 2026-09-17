"""需求生命周期的应用服务。"""

from __future__ import annotations

import csv
import io
from collections.abc import Mapping

from requirement_agent.agents.analyze_agent import AnalyzeAgent
from requirement_agent.agents.extract_agent import ExtractAgent
from requirement_agent.agents.risk_agent import RiskAgent
from requirement_agent.application.capability_match_service import CapabilityMatchService
from requirement_agent.application.run_tracking import RunTracking
from requirement_agent.infrastructure.llm.invocation import bind_run_id
from requirement_agent.domain.requirement import RequirementSource
from requirement_agent.infrastructure.db.repositories import (
    RequirementMasterRepository,
    RequirementSourceRepository,
)
from requirement_agent.infrastructure.parser.document_parser import DocumentParser
from requirement_agent.workflows.graphs import run_analysis


def _csv_cell(value: object) -> str:
    """CSV 单元格取值：多值字段用顿号连接，None 归一为空串。"""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return "、".join(str(item) for item in value if item is not None)
    return str(value)


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
        capability_matcher: CapabilityMatchService | None = None,
        run_tracking: RunTracking | None = None,
    ) -> None:
        self.source_repo = source_repo or RequirementSourceRepository()
        self.master_repo = master_repo or RequirementMasterRepository()
        self.extract_agent = extract_agent or ExtractAgent()
        self.analyze_agent = analyze_agent or AnalyzeAgent()
        self.risk_agent = risk_agent or RiskAgent()
        self.document_parser = document_parser or DocumentParser()
        self.capability_matcher = capability_matcher or CapabilityMatchService()
        self.run_tracking = run_tracking or RunTracking()

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
        return self._analyze_and_mark_pending(saved_source)

    def accept_requirement(self, source: RequirementSource) -> RequirementSource:
        """只落库、不分析：写入 requirement_source 并保持 received 状态。

        渠道接入的第一段。之后由 `RequirementAnalysisTask` 异步调用 `process_requirement`，
        这样 Webhook 能在渠道要求的超时内（飞书是 3 秒）立即返回，
        不会因为 LLM 分析图耗时而被判超时、进而触发渠道重投。
        """
        return self.source_repo.save(source)

    def process_requirement(self, source_id: int) -> dict[str, object]:
        """按 source_id 跑分析并置为待审核（异步路径的第二段）。

        幂等：来源若已不是 received/failed（已分析过或已在流水线上），只回现状、
        不重复跑图 —— 所以重复消费同一条 outbox 事件是安全的。
        """
        source = self.source_repo.get_by_id(source_id)
        if source is None:
            raise ValueError(f"requirement source not found: {source_id}")
        if source.processing_status not in {"received", "failed"}:
            return {
                "source_id": source.id,
                "idempotency_key": source.idempotency_key,
                "source_type": source.source_type,
                "status": source.processing_status,
            }
        return self._analyze_and_mark_pending(source)

    def _analyze_and_mark_pending(self, saved_source: RequirementSource) -> dict[str, object]:
        """清洗规整文本 → 跑分析图 → 回填 metadata → 置 pending_review。

        同步路径（`submit_requirement`）与异步路径（`process_requirement`）共用本方法，
        保证两条入口的分析行为完全一致。
        """
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

        # —— 运行追踪（B2.1）——
        # **run 由这里建，不由图建。** 图节点只产出事件（进 state 的 `run_events` 通道），
        # 生命周期归 Application 层 —— 理由见 `application/run_tracking.py` 的模块 docstring。
        #
        # `start_analysis` 失败返回 None（追踪是观测设施，不该拖垮分析），
        # 后续所有 record/finish 在 run_id 为 None 时都是空操作。
        run_id = self.run_tracking.start_analysis(
            source_id=saved_source.id,
            meta={"source_type": saved_source.source_type},
        )

        # —— Agent 编排统一走 LangGraph 分析图（抽取→检索→冲突分析→风险→决策）——
        try:
            # 把这次分析调的所有模型关联到 run（B3.1）—— provider 记调用时读它。
            # 用 ContextVar 而不是往每个函数签名里加参数：那要改整条调用链，
            # 而这是纯观测信息。见 `infrastructure/llm/invocation.py`。
            with bind_run_id(run_id):
                result = run_analysis(
                    source_id=saved_source.id,
                    source_text=standardized_text,
                    source_type=saved_source.source_type,
                    requester_name=saved_source.requester_name,
                )
        except Exception as exc:
            # **失败要能定位到节点** —— 节点名由 `event_nodes.NodeFailure` 带出来
            # （异常路径上 state 通道会丢，所以只能随异常传递）。
            node = getattr(exc, "node", None)
            self.run_tracking.fail(run_id, error=exc, node=node)
            raise

        # 事件与工具调用落库。工具调用单独落一张表 —— metadata 里那份是给
        # 「分析→审核」跨进程传递用的，而这张表是给「按 run 反查」用的，两者不互相替代。
        self.run_tracking.record_events(run_id, list(result.get("run_events") or []))
        self.run_tracking.record_tool_calls(run_id, list(result.get("tool_calls") or []))
        self.run_tracking.finish(run_id)

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
        # —— 降级标记（B3.1b）——
        # ⚠️ 追加实施文档 §4.5：「**风险和冲突判断降级后必须标记，不得静默当作确定结果**」。
        #
        # 结论本身照常产出（降级好过什么都给不出），但必须让审核人知道
        # 「这不是主模型给的」—— 他据此决定要不要更谨慎，而不是当成一次正常判断。
        degraded_fields = [
            name for name, payload in (("analysis", analysis), ("risk", risk))
            if payload.get("degraded")
        ]
        if degraded_fields:
            metadata["degradation"] = {
                "degraded": True,
                "fields": degraded_fields,
                "reason": analysis.get("degraded_reason") or risk.get("degraded_reason"),
            }
        # —— 能力/条件候选与词表比对（方案批次 2）——
        # 只写 pending_confirmation 提案与匹配记录，**不写任何正式数据**。
        # 这是 A 级「自动归档」范畴：记的是事实（模型抽出了什么、匹配上了什么），
        # 不是业务决策（哪些能力正式成立）—— 后者由人工在审核页确认。
        metadata["capability_match"] = self.capability_matcher.match(
            extracted, source_id=saved_source.id
        )
        # —— 工具调用留痕（B3）——
        # 分析与落库**分处两个进程/两次调用**（分析在 outbox 任务里、落库在审核时），
        # 所以「这次分析调了哪些工具、各花了多久」必须随 metadata 一起存下来，
        # 否则事后只能靠日志时间戳猜。形状见 `tools/invoker.tool_call_record`。
        tool_calls = list(result.get("tool_calls") or [])
        if tool_calls:
            metadata["tool_calls"] = tool_calls
        # —— 检索证据（B4 批 4）——
        # 每条候选的余弦、查询级落差、当时生效的校准基线、过滤模式。**候选不裁剪。**
        # 落它的理由见 `workflows/agents_nodes._retrieval_record`：判定用了两把锁之后，
        # 要复校阈值就必须知道「当时到底召回了什么、各自多少分」，而这些事后查不回来。
        #
        # ⚠️ 这里**删掉了** `metadata["retrieval_filters"]`。它原先算好一堆维度
        # （渠道/部门/领域/密级）却**零消费者** —— 没有任何代码读它回填检索，
        # 前端也没读。留着会让人以为「检索真的按这些维度过滤了」。
        # 现在过滤的真实状态在 `retrieval.filters` 里，那是**实际发生**的而不是「本来是这些」。
        retrieval = dict(result.get("retrieval") or {})
        if retrieval:
            metadata["retrieval"] = retrieval
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

    def list_requirements(
        self,
        *,
        filters: Mapping[str, object] | None = None,
        limit: int = 100,
    ) -> list[dict[str, object]]:
        """返回主需求列表（含领域/当前版本/功能行数/来源人/渠道/部门/密级/提交时间）。

        供「需求库」表格视图与 CSV 导出使用；筛选条件见
        `RequirementMasterRepository._build_master_filter`。不带筛选时行为与加筛选前一致。
        """
        rows = self.master_repo.list_with_source_context(limit=limit, filters=filters)
        return [self._present_requirement(item) for item in rows]

    @staticmethod
    def _present_requirement(item: Mapping[str, object]) -> dict[str, object]:
        """把 repository 的聚合行裁剪成对外字段（列表与 CSV 共用同一份映射）。

        多值字段（渠道/部门/领域/密级）一律返回**数组**而非首元素——前端要用它们聚合
        出筛选下拉，只取第一个会让选项残缺。`business_domain` 保留为兼容字段（取首个），
        新代码请用 `business_domains`。
        """
        domains = list(item.get("business_domains") or [])
        return {
            "requirement_key": item["requirement_key"],
            "requirement_name": item["requirement_name"],
            "final_requirement": item["final_requirement"],
            "status": item["status"],
            "business_domain": domains[0] if domains else "general",
            "business_domains": domains,
            "current_version": item.get("current_version", 0),
            "feature_count": item.get("feature_count", 0),
            "requester_names": list(item.get("requester_names") or []),
            "source_types": list(item.get("source_types") or []),
            "departments": list(item.get("departments") or []),
            "sensitivity_levels": list(item.get("sensitivity_levels") or []),
            "first_source_submitted_at": item.get("first_source_submitted_at"),
            "latest_source_submitted_at": item.get("latest_source_submitted_at"),
        }

    # CSV 的列顺序与中文表头；改这里要同步改 tests/unit/test_requirements_export.py
    CSV_COLUMNS: tuple[tuple[str, str], ...] = (
        ("requirement_key", "需求编号"),
        ("requirement_name", "需求名称"),
        ("final_requirement", "最终需求"),
        ("status", "状态"),
        ("business_domains", "业务领域"),
        ("current_version", "当前版本"),
        ("feature_count", "功能数"),
        ("source_types", "来源渠道"),
        ("requester_names", "来源人"),
        ("departments", "部门"),
        ("sensitivity_levels", "密级"),
        ("latest_source_submitted_at", "最近提交时间"),
    )

    def export_requirements_csv(
        self,
        *,
        filters: Mapping[str, object] | None = None,
        limit: int = 100,
    ) -> str:
        """把需求库当前视图导出为 CSV 文本（带 UTF-8 BOM，Excel 打开中文不乱码）。

        用标准库 `csv` 生成而非手工拼接字符串：需求正文里出现逗号、引号、换行是常态，
        手拼必然出错。
        """
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow([header for _key, header in self.CSV_COLUMNS])
        for item in self.list_requirements(filters=filters, limit=limit):
            writer.writerow([_csv_cell(item.get(key)) for key, _header in self.CSV_COLUMNS])
        # 函数体首行这个不可见字符是 UTF-8 BOM（U+FEFF）：Excel 靠它识别编码，
        # 没有它中文列名会乱码。编辑器里看不见，不要顺手删掉。
        return "﻿" + buffer.getvalue()
