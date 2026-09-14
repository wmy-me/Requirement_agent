"""运维（ops）HTTP 路由。

与 `/api/v1/health/*` 的分工：health 是**只读探活**，这里是**运维操作面** ——
查看异步队列积压、处理死信。将来数据保留（清理/归档）的入口也归这里。

为什么需要它：`outbox_event` 的死信此前**只能看、不能动，而且看不见** ——
唯一的 `GET /tasks/dead-letter` 挂在**未部署**的独立 worker（:8200）上，主 API 不挂载，
前端零引用；`OutboxRepository` 也没有任何重置/放弃方法，运维只能手工 `UPDATE`。

⚠️ 本域目前**没有鉴权**（阶段 6 的鉴权批次尚未做），处境与现有全部写端点相同 ——
不是新增暴露面，但接入鉴权时必须一并纳入保护范围。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, status

from requirement_agent.api.dependencies import outbox_repo
from requirement_agent.common.time import as_display_iso

router = APIRouter()


def _dead_letter_dicts(*, limit: int) -> list[dict[str, object]]:
    """死信明细。

    只带出运维需要的字段：`payload` 可能很大、也可能含业务正文，列表里不整段带出。
    """
    return [
        {
            "id": event.id,
            "event_type": event.event_type,
            "aggregate_type": event.aggregate_type,
            "aggregate_id": event.aggregate_id,
            "retries": event.retries,
            "last_error": event.last_error,
            "created_at": as_display_iso(event.created_at),
        }
        for event in outbox_repo.list_dead_letters(limit=limit)
    ]


@router.get("/api/v1/ops/outbox")
async def get_outbox_status(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, object]:
    """异步队列状态：各状态计数 + 死信明细 + 消费循环快照，一次拿全供面板渲染。

    消费循环挂在 `app.state` 上（它由 lifespan 启停，不是模块级单例）；未启动时该段
    返回 `None`，而不是伪造一份全零统计——「没跑」和「跑了但没干活」是两回事。
    """
    consumer = getattr(request.app.state, "outbox_consumer", None)
    return {
        "counts": outbox_repo.count_by_status(),
        "dead_letters": _dead_letter_dicts(limit=limit),
        "consumer": consumer.stats() if consumer is not None else None,
    }


@router.post("/api/v1/ops/outbox/dead-letters/{event_id}/retry")
async def retry_dead_letter(event_id: int) -> dict[str, object]:
    """把一条死信重置回 pending 等待重投；不存在或**不是死信**返回 404。

    只接受死信：否则一次误点会把正在处理或已完成的事件也重置，造成重复执行
    （embedding 重复写、文档重复切片）。
    """
    if not outbox_repo.retry_dead_letter(event_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="dead letter not found")
    return {"status": "requeued", "event_id": event_id}


@router.post("/api/v1/ops/outbox/dead-letters/{event_id}/discard")
async def discard_dead_letter(event_id: int) -> dict[str, object]:
    """放弃一条死信：置为终态 `discarded`，保留行与错误信息留档；同上只认死信。"""
    if not outbox_repo.discard_dead_letter(event_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="dead letter not found")
    return {"status": "discarded", "event_id": event_id}
