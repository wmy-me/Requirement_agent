"""分析运行的生命周期与事件落库（B2.1）。

**为什么在 Application 层，而不是在 `run_analysis` 里。** 方案原写的是
「`graphs.py` 的 `run_analysis` 入口建 run、出口收尾」，实施时改了主意，理由有两条：

1. **`run_analysis` 只有一个生产调用方**（`RequirementService._analyze_and_mark_pending`），
   所以把生命周期放在调用方并不是重复劳动；而方案当时假设的
   「它是所有分析的必经之路」**不成立** —— `/api/v1/agent/run` 走的是一条
   独立的内联管线（`routes/agent.py`），根本不经过图。
2. **让图建 run 会让 `run_analysis` 变成写库的**，而分析图的节点在 B3 就定下
   「纯计算，不写库」的纪律。更实际的问题：`tests/unit/test_requirement_graph.py`
   有三处**直接调 `run_analysis`**，图一旦写库，那三条纯图测试每跑一次就往库里写一行。

所以分工是：**图产出事件（进 state 通道），Application 层管 run 的生死与落库。**

## 追踪失败不抛

运行追踪是**观测设施**。它坏了不该让分析坏掉 —— 但**必须留下可见的痕迹**，
所以每次失败都打一条 `warning`，而不是静默吞掉。这与 B3 定下的
「降级必须可见」是同一条纪律：静默降级等于没有这个机制。

具体做法：所有公开方法在 `run_id` 为 None（上游没建成功）时直接返回，
内部异常一律捕获 + 告警，**绝不向上抛**。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, TypeVar

from requirement_agent.domain.agent_run import RunEvent
from requirement_agent.infrastructure.db.repositories import AgentRunRepository

logger = logging.getLogger(__name__)

__all__ = ["RunTracking"]

T = TypeVar("T")


class RunTracking:
    """把一次分析变成可查询的 run + 事件流。"""

    def __init__(self, repo: AgentRunRepository | None = None) -> None:
        self.repo = repo or AgentRunRepository()

    # ── 生命周期 ──────────────────────────────────────────────────────────

    def start_analysis(
        self, *, source_id: int | None, meta: dict[str, Any] | None = None
    ) -> str | None:
        """建 run、立刻发 `run_started`，返回 `run_id`（失败返回 None）。

        立刻发 `run_started` 而不是等到第一个节点：这样「run 建了但一个节点都没跑」
        这件事在事件流里是**可见的**（只有 `run_started` 就没了），
        而不是一个空 run 让人猜。
        """
        created = self._guard(
            "start_analysis",
            lambda: self.repo.create_run(run_type="analysis", source_id=source_id, meta=meta),
        )
        if not created:
            return None
        run_id = str(created["run_id"])
        self.record_events(
            run_id,
            [RunEvent("run_started", payload={"source_id": source_id})],
        )
        self._guard("mark_running", lambda: self.repo.mark_running(run_id))
        return run_id

    def record_events(self, run_id: str | None, events: list[Any]) -> None:
        """落一批事件。入参是 `RunEvent` 或已经是 dict 的形态。"""
        if not run_id or not events:
            return
        shaped = [
            event.to_event_dict() if isinstance(event, RunEvent) else dict(event)
            for event in events
        ]
        self._guard("record_events", lambda: self.repo.append_events(run_id, shaped))

    def record_one(self, run_id: str | None, event: Any) -> int | None:
        """落**一条**事件并返回它拿到的 `sequence`，供 SSE 的 `id:` 行用。

        与 `record_events` 分开是有原因的：这条路径要的是「序号本身」，
        而批量路径只关心「有没有写进去」。序号拿不到（追踪故障）时返回 `None`，
        调用方据此发一个**没有 `id:`** 的帧 —— 那仍然是合法的 SSE。
        """
        if not run_id:
            return None
        shaped = event.to_event_dict() if isinstance(event, RunEvent) else dict(event)
        rows = self._guard("record_one", lambda: self.repo.append_events(run_id, [shaped]))
        if not rows:
            return None
        return int(rows[0]["sequence"])

    def record_tool_calls(self, run_id: str | None, tool_calls: list[Any]) -> None:
        """把 `tool_call_record` 的形状落成 `tool_invocation` 行。"""
        if not run_id or not tool_calls:
            return
        self._guard(
            "record_tool_calls",
            lambda: self.repo.record_tool_invocations(run_id, list(tool_calls)),
        )

    def fail(self, run_id: str | None, *, error: BaseException, node: str | None) -> None:
        """收尾成 `failed`。

        `node` 是**出错的那个节点**（由 `event_nodes.NodeFailure` 带出来）——
        「失败能定位到节点」这条验收就是靠它。拿不到时留 None，不编一个。
        """
        if not run_id:
            return
        self.record_events(
            run_id,
            [RunEvent("run_failed", node=node, payload={"error": str(error)})],
        )
        self._guard(
            "fail_run",
            lambda: self.repo.finish_run(
                run_id, status="failed", error=str(error), current_node=node
            ),
        )

    def finish(self, run_id: str | None, *, current_node: str = "decide") -> None:
        """分析跑完、进入待人审 —— 状态是 **`waiting_review` 而不是 `completed`**。

        为什么：分析的产物是「一条待审来源」，人要审完才算真的结束。
        置成 `completed` 会让「这条需求分析完了吗」这个问题在数据库里失去答案
        （`requirement_source.processing_status` 此时是 `pending_review`，
        两边对不上会让人以为是两个不同的进度）。
        """
        if not run_id:
            return
        self.record_events(run_id, [RunEvent("run_completed", node=current_node)])
        self._guard(
            "finish_run",
            lambda: self.repo.finish_run(
                run_id, status="waiting_review", current_node=current_node
            ),
        )

    # ── 护栏 ──────────────────────────────────────────────────────────────

    def _guard(self, what: str, action: Callable[[], T]) -> T | None:
        """跑一个追踪动作，**任何异常都只告警、不上抛**（模块 docstring 有理由）。"""
        try:
            return action()
        except Exception as exc:  # noqa: BLE001 —— 观测设施不该拖垮业务
            logger.warning(
                "event=run_tracking_failed what=%s error=%s: %s",
                what,
                type(exc).__name__,
                exc,
            )
            return None
