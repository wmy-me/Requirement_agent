"""Agent 运行追踪（B2.1）。

在它之前，需求分析**跑完零留痕**：没有 run 行、没有节点时间线、没有失败记录，
结果只被拆散塞进 `requirement_source.metadata` 的几个 JSONB 键里。

这个文件钉住四件事：
1. 事件流**有序、不重、可回放**（`UNIQUE(run_id, sequence)` + `after_seq`）；
2. run 的**生死与失败定位**（`current_node` 指向出错的那个节点）；
3. 两条分析路径**都**被追踪（LangGraph 图 + `/agent/run` 的内联管线）；
4. 兼容红线：**SSE 帧在 `seq=None` 时逐字节不变**。

**不依赖真 LLM**：分析图里的三个 Agent 用假件替换。追踪是纯编排层的事，
用真模型只会让测试变慢且不确定。
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from requirement_agent.agents.analyze_agent import AnalysisResult
from requirement_agent.agents.extract_agent import ExtractedRequirement
from requirement_agent.agents.risk_agent import RiskAssessment
from requirement_agent.application.requirement_service import RequirementService
from requirement_agent.application.run_tracking import RunTracking
from requirement_agent.domain.agent_run import RunEvent, redact_payload
from requirement_agent.domain.requirement import RequirementSource
from requirement_agent.infrastructure.db.repositories import AgentRunRepository
from requirement_agent.infrastructure.db.session import SessionLocal
from requirement_agent.workflows import agents_nodes
from requirement_agent.workflows.event_nodes import NodeFailure


# ── 假件：让分析图不碰真模型 ──────────────────────────────────────────────


def _extracted() -> ExtractedRequirement:
    return ExtractedRequirement(
        requirement_title="门店巡检",
        summary="按区域生成巡检计划并拍照上传",
        business_domain="workflow",
        tags=["巡检"],
        requirements=["按区域生成巡检计划"],
        raw_text="按区域生成巡检计划并拍照上传。",
    )


class FakeExtractAgent:
    def extract(self, *args, **kwargs) -> ExtractedRequirement:
        return _extracted()


class FakeAnalyzeAgent:
    def analyze(self, extracted, candidates=None, **kwargs) -> AnalysisResult:
        return AnalysisResult(
            duplicate=False, related=False, conflict=False, independent=True,
            reasoning="假件", candidates=[],
        )


class FakeRiskAgent:
    def assess(self, extracted) -> RiskAssessment:
        return RiskAssessment(
            quality_risk="low", change_risk="low", technical_impact_risk="low", confidence=0.9
        )


@pytest.fixture
def no_llm(monkeypatch):
    """把三个 Agent 换成假件 —— 分析图照跑，只是不花 LLM 调用。"""
    monkeypatch.setattr(agents_nodes, "ExtractAgent", FakeExtractAgent)
    monkeypatch.setattr(agents_nodes, "AnalyzeAgent", FakeAnalyzeAgent)
    monkeypatch.setattr(agents_nodes, "RiskAgent", FakeRiskAgent)
    # 检索走关键词分支（不调 embedding），并把候选固定成两条
    monkeypatch.setattr(
        agents_nodes,
        "invoke",
        lambda name, params, consumer=None: type(
            "R", (), {"result": [
                {"requirement_key": "REQ-000001", "requirement_name": "历史", "vector_similarity": 0.80},
                {"requirement_key": "REQ-000002", "requirement_name": "历史2", "vector_similarity": 0.60},
            ], "status": type("S", (), {"value": "success"})(), "duration_ms": 1.0, "message": None}
        )(),
    )


def _source(marker: str) -> RequirementSource:
    return RequirementSource(
        source_type="web",
        original_text=f"{marker}：按区域生成巡检计划并拍照上传。",
        requester_name="B2.1 测试",
        idempotency_key=f"b21-{uuid.uuid4().hex}",
    )


@pytest.fixture
def cleanup():
    """删掉这次造出来的来源（run 随外键级联删）。"""
    made: list[int] = []
    yield made
    if made:
        with SessionLocal() as session:
            session.execute(
                text("DELETE FROM requirement_source WHERE id = ANY(:ids)"), {"ids": made}
            )
            session.commit()


def _submit(no_llm, cleanup) -> tuple[int, str]:
    """跑一遍真实的分析链路，返回 `(source_id, run_id)`。"""
    result = RequirementService().submit_requirement(_source("B2.1"))
    source_id = int(result["source_id"])
    cleanup.append(source_id)
    runs = AgentRunRepository().list_by_source(source_id)
    assert runs, "分析跑完必须留下一个 run"
    return source_id, str(runs[0]["run_id"])


# ── ① 事件流 ──────────────────────────────────────────────────────────────


def test_event_sequence_is_dense_and_unique(no_llm, cleanup) -> None:
    """同一 run 的 `sequence` 从 1 连续递增、不重。

    「有序且不重复」由 `UNIQUE (run_id, sequence)` 在**数据库层**保证，
    这里验证应用层分配得也对（从 1 开始、中间不跳号）。
    """
    _, run_id = _submit(no_llm, cleanup)
    events = AgentRunRepository().list_events(run_id)

    sequences = [event["sequence"] for event in events]
    assert sequences == list(range(1, len(sequences) + 1)), (
        f"序号必须从 1 连续递增，实际 {sequences}"
    )


def test_after_seq_replay_returns_exactly_the_tail(no_llm, cleanup) -> None:
    """`after_seq=N` **排他**地只返回 `> N`，且顺序正确。"""
    _, run_id = _submit(no_llm, cleanup)
    repo = AgentRunRepository()
    all_events = repo.list_events(run_id)
    assert len(all_events) >= 5, "完整分析应当有足够多的事件可供切分"

    cut = all_events[2]["sequence"]
    tail = repo.list_events(run_id, after_seq=cut)

    assert [event["sequence"] for event in tail] == [
        event["sequence"] for event in all_events if event["sequence"] > cut
    ]
    assert all(event["sequence"] > cut for event in tail), "after_seq 是排他的"


def test_analysis_run_is_created_and_closed(no_llm, cleanup) -> None:
    """走一遍真实链路：run 的状态、起止时间、当前节点都要落到位。"""
    _, run_id = _submit(no_llm, cleanup)
    run = AgentRunRepository().get_run(run_id)

    assert run["run_type"] == "analysis"
    assert run["status"] == "waiting_review", (
        "分析跑完是 waiting_review 而不是 completed —— 人要审完才算真的结束"
    )
    assert run["started_at"] is not None and run["ended_at"] is not None
    assert run["current_node"] == "decide"

    kinds = [event["event_type"] for event in AgentRunRepository().list_events(run_id)]
    assert kinds[0] == "run_started"
    assert kinds[-1] == "run_completed"
    for node in ("extract", "retrieve", "analyze", "risk", "decide"):
        assert "node_started" in kinds, f"{node} 的启动事件缺失"


def test_event_stream_covers_every_node_in_order(no_llm, cleanup) -> None:
    """节点事件的**顺序**要与图的执行顺序一致 —— 它是时间线，不是集合。"""
    _, run_id = _submit(no_llm, cleanup)
    started = [
        event["node"]
        for event in AgentRunRepository().list_events(run_id)
        if event["event_type"] == "node_started"
    ]
    assert started == ["extract", "retrieve", "analyze", "risk", "decide"]


# ── ② 失败定位 ────────────────────────────────────────────────────────────


def test_failed_analysis_records_the_node(no_llm, cleanup, monkeypatch) -> None:
    """**失败要能定位到节点。**

    节点抛错时 state 通道会丢（`invoke` 直接抛），所以节点名只能随异常带出来 ——
    `NodeFailure` 就是干这个的，`current_node` 就是它落地的样子。
    """
    from requirement_agent.tools.base import ToolResult

    monkeypatch.setattr(
        agents_nodes, "invoke", lambda *a, **k: ToolResult.error("数据库连接被拒绝")
    )

    # **按 idempotency_key 精确定位**，不靠「找最近的 failed run」——
    # 后者会捡到别的测试或上一次运行留下的行，测试之间互相污染。
    source = _source("B2.1-失败")
    with pytest.raises(NodeFailure, match="retrieve"):
        RequirementService().submit_requirement(source)

    with SessionLocal() as session:
        source_id = session.execute(
            text("SELECT id FROM requirement_source WHERE idempotency_key = :k"),
            {"k": source.idempotency_key},
        ).scalar()
        assert source_id is not None, "来源在跑图前就 save 了，应当能查到"
        cleanup.append(int(source_id))

        row = session.execute(
            text(
                "SELECT status, current_node, error FROM agent_run WHERE source_id = :i"
            ),
            {"i": source_id},
        ).mappings().first()

    assert row is not None, "失败也必须留下 run —— 否则「失败能定位」无从谈起"
    assert row["status"] == "failed"
    assert row["current_node"] == "retrieve", "失败必须指向出错的那个节点"
    assert "数据库连接被拒绝" in str(row["error"]), (
        "错误文本要带底层原因 —— 只说「节点失败」等于没写"
    )


# ── ③ 工具调用 ────────────────────────────────────────────────────────────


def test_tool_invocation_mirrors_tool_call_record() -> None:
    """`tool_invocation` 的列与 `tool_call_record` 的键**一一对应**。

    留痕的形状是 B3 定下的；这里不另发明一套，否则就是两份要同步的事实源
    （而本项目对「两份事实源」的代价有过多次教训）。这条测试就是防它们漂移。
    """
    from requirement_agent.tools.invoker import tool_call_record
    from requirement_agent.tools.base import ToolResult

    record = tool_call_record(
        "search_requirements",
        {"query": "x", "limit": 5},
        ToolResult.success([{"requirement_key": "REQ-1", "vector_similarity": 0.8}]),
    )
    expected_keys = {"tool", "params", "status", "duration_ms", "count", "message", "sample"}
    assert set(record) == expected_keys, (
        "tool_call_record 的键变了 —— 同步改 AgentRunRepository.record_tool_invocations "
        "与 migration 021 的列，别让两处漂移"
    )


def test_tool_invocation_lands_for_a_real_analysis(no_llm, cleanup) -> None:
    """真实链路里工具调用确实落库，且带着 sample。"""
    _, run_id = _submit(no_llm, cleanup)
    rows = AgentRunRepository().list_tool_invocations(run_id)

    assert len(rows) == 1, "这次分析只调了 search_requirements 一个工具"
    row = rows[0]
    assert row["tool_name"] == "search_requirements"
    assert row["status"] == "success"
    assert row["result_summary"]["count"] == 2
    assert row["result_summary"]["sample"][0]["requirement_key"] == "REQ-000001"


# ── ④ 幂等 ────────────────────────────────────────────────────────────────


def test_reprocessing_the_same_source_is_idempotent(no_llm, cleanup) -> None:
    """同一条来源被处理两次，**业务写入只有一次**。

    这条守的是追加文档 §3.6 的「重复消费事件不会产生重复业务写入」：
    靠的是 `process_requirement` 对已处理来源短路，而不是新表。
    """
    service = RequirementService()
    source = _source("B2.1-幂等")
    first = service.submit_requirement(source)
    source_id = int(first["source_id"])
    cleanup.append(source_id)

    second = service.process_requirement(source_id)

    assert second["status"] == "pending_review", "第二次应当直接回现状"
    runs = AgentRunRepository().list_by_source(source_id)
    assert len(runs) == 1, "短路之后不该再建第二个 run"


# ── ⑤ 兼容红线 ────────────────────────────────────────────────────────────


def test_sse_frame_shape_is_unchanged_without_seq() -> None:
    """**`seq=None` 时 SSE 帧逐字节不变。**

    现有 6 个事件名与 data 形状都被契约与前端消费，改名/改形状是破坏性的。
    带 `id:` 是新能力，不带时必须与改造前完全一致。
    """
    from requirement_agent.api.routes.agent_chat import _sse

    assert _sse("step", {"step": "extract"}) == (
        'event: step\ndata: {"step": "extract"}\n\n'
    )
    # 带 seq 时只多一行 id:，其余完全一致
    framed = _sse("step", {"step": "extract"}, seq=7)
    assert framed == 'id: 7\nevent: step\ndata: {"step": "extract"}\n\n'
    assert framed.split("\n", 1)[1] == _sse("step", {"step": "extract"})


def test_narrative_is_not_a_tracked_event() -> None:
    """`narrative` **不落库、不占序号**。

    它是逐 token 推送的，一个长回答会产生成百上千条事件。断线恢复靠
    `artifacts` + 最终那条完整 narrative，**不是逐字回放** —— 这条约定必须钉住，
    否则「after_seq 回放」会被理解成能逐字重放。
    """
    from requirement_agent.api.routes.agent_chat import _SSE_TO_RUN_EVENT

    assert "narrative" not in _SSE_TO_RUN_EVENT
    assert set(_SSE_TO_RUN_EVENT) == {"step", "done", "error"}


# ── ⑥ 脱敏与截断 ──────────────────────────────────────────────────────────


def test_payload_redaction_drops_sensitive_keys() -> None:
    """追加文档 §7.2：事件内容不得泄漏 API Key、完整 Prompt 或敏感原文。

    脱敏在**写入边界**做（`AgentRunRepository.append_events`），不在生产端 ——
    事件也可能从别的路径进来，在每个生产端各脱一次迟早漏一个。
    """
    cleaned = redact_payload(
        {
            "note": "正常内容",
            "api_key": "sk-live-xxx",
            "authorization": "Bearer yyy",
            "prompt": "很长的提示词" * 100,
            "raw_text": "需求原文",
            "nested": {"token": "abc", "ok": 1},
        }
    )

    assert cleaned["note"] == "正常内容"
    for key in ("api_key", "authorization", "prompt", "raw_text"):
        assert "已脱敏" in str(cleaned[key]), f"{key} 必须被整键替换，而不是打码"
    assert "已脱敏" in str(cleaned["nested"]["token"])
    assert cleaned["nested"]["ok"] == 1


def test_payload_is_truncated_when_too_long() -> None:
    """超长 payload 整体截断 —— 事件要长期保留、要经 SSE 推送，不能无节制。"""
    cleaned = redact_payload({"blob": "x" * 5000})
    assert cleaned.get("truncated") is True
    assert len(str(cleaned["preview"])) < 5000


def test_run_event_shapes_to_a_plain_dict() -> None:
    """事件进 state 通道的是**普通 dict**，与 `tool_calls` 通道同一形状。"""
    assert RunEvent("node_started", node="extract").to_event_dict() == {
        "event_type": "node_started",
        "node": "extract",
        "payload": {},
    }


# ── 上下文传播：工具内部也要能读到 run 绑定（B3.1）─────────────────────────


def test_tool_worker_thread_inherits_contextvars() -> None:
    """**工具的工作线程必须继承 contextvars。**

    实测撞到的 bug：`BaseTool._execute_with_timeout` 把 `execute` 跑在一个
    **原生 `threading.Thread`** 里，而原生线程**不复制 contextvars
    （`anyio.to_thread.run_in_threadpool` 会，原生线程不会）。

    后果不是「少记一条日志」，而是**任何依赖 ContextVar 的横切机制在工具内静默失效**：
    B3.1 实测里，检索工具内部的 embedding 调用记下的 `run_id` 是 NULL，
    而同一次分析里图节点直接调的 extract/analyze/risk 都有值 —— 因为只有前者穿了工具层。
    """
    import contextvars

    from requirement_agent.tools.base import BaseTool, ToolInput, ToolResult

    probe: contextvars.ContextVar[str] = contextvars.ContextVar("probe", default="<未传播>")

    class _ProbeInput(ToolInput):
        pass

    # ⚠️ **刻意不加 `@register`**：注册是全局的，会污染工具名录，
    # 而 `test_tool_contract.py` 有三条断言名录必须与约定完全一致
    # （实测加了之后那三条立刻红）。`run()` 是实例方法，不需要注册也能用。
    class _ProbeTool(BaseTool):
        """只用来验证上下文传播；不注册，所以名录不受影响。"""

        name = "context_probe"
        description = "测试用：读一个 ContextVar"
        input_model = _ProbeInput
        output_schema: dict = {"type": "object", "properties": {"seen": {"type": "string"}}}
        allowed_consumers = ("analysis",)

        def execute(self, params) -> ToolResult:
            # 这里跑在工作线程里 —— 断言它看得见主线程设的值
            return ToolResult.success({"seen": probe.get()})

    token = probe.set("主线程设的值")
    try:
        seen = _ProbeTool().run({}).result["seen"]
    finally:
        probe.reset(token)

    assert seen == "主线程设的值", (
        "工具的工作线程丢掉了 contextvars —— 检查 `_execute_with_timeout` 是否用了 "
        "`contextvars.copy_context()`。退回原样会让工具内的一切横切机制静默失效。"
    )
