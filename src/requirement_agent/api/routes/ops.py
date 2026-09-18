"""运维（ops）HTTP 路由。

与 `/api/v1/health/*` 的分工：health 是**只读探活**，这里是**运维操作面** ——
查看异步队列积压、处理死信。将来数据保留（清理/归档）的入口也归这里。

为什么需要它：`outbox_event` 的死信此前**只能看、不能动，而且看不见** ——
唯一的 `GET /tasks/dead-letter` 挂在**未部署**的独立 worker（:8200）上，主 API 不挂载，
前端零引用；`OutboxRepository` 也没有任何重置/放弃方法，运维只能手工 `UPDATE`。

鉴权：B1 起 `/api/v1/ops/*` 的非 GET 请求需要 `ops` 档次（`admin` 与 `system_worker`
具备），GET 一律 `read`。**本段原先写着「本域没有鉴权」—— 那句在 B1 之后已过时。**
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, status

from requirement_agent.api.dependencies import feishu_client, model_invocation_repo, outbox_repo
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


@router.get("/api/v1/ops/worker")
async def get_worker_status(request: Request) -> dict[str, object]:
    """后台消费循环的健康状况。

    与 `/ops/outbox` 的分工：那个是**队列**视角（积压多少），这个是**工人**视角
    （还活着吗、卡住了吗）。两者都会用到「各状态计数」，但回答的问题不同。

    ⚠️ **`stale_processing` 是这里最要紧的一个数。** 只看状态计数看不出来：
    消费者崩掉之后它认领的行会永远停在 `processing`，队列看上去「有在干活」。
    超过 `stale_timeout_seconds` 还没收尾的就是卡住的。

    ⚠️ `consumer` 为 `null` 说明**这个 API 进程**没跑内嵌消费循环。它不能说明
    「系统没有消费者」—— 生产形态是独立 Worker（`python -m requirement_agent.workers`），
    那个进程的状态不在本端点里。判断有没有在消费，要看队列是否在推进
    （`counts.pending` 与 `completed` 的变化），而不是只看这个字段。
    """
    consumer = getattr(request.app.state, "outbox_consumer", None)
    return {
        "consumer": consumer.stats() if consumer is not None else None,
        "counts": outbox_repo.count_by_status(),
        "stale_processing": outbox_repo.count_stale_processing(),
        "stale_timeout_seconds": outbox_repo.stale_timeout_seconds,
    }


@router.get("/api/v1/ops/models")
async def list_model_invocations(
    task_type: str | None = None,
    run_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, object]:
    """模型调用记录 + **路由配置诊断**。

    「我明明给 analyze 配了模型，怎么没生效」—— `routing.unrecognized` 就是答案：
    它列出配了但**未被采用**的条目及原因（provider 名拼错 / task_type 拼错 / 缺字段）。
    配置是手写 JSON，写错键名是常事，而这个诊断让「配了没生效」不必靠读代码猜。

    `run_id` 是给**智能分析页的 Run 详情**用的（2026-09-18 新增）：那个页面要的是
    「这次运行调了哪些模型」，此前只能拉全量自己筛 —— 而「前端不聚合后端数据」
    是本项目的既定原则。带上 `run_id` 时**返回顺序是正序**（与节点时间线对齐），
    不带时是倒序（最近优先），见 `ModelInvocationRepository.list_by_run` 的说明。

    ⚠️ `GET /api/v1/agent/runs/{id}/invocations` 的 `models` 字段**恒为空数组** ——
    模型调用明细在这里拿，不在那里。
    """
    from requirement_agent.infrastructure.llm.model_registry import ModelRegistry

    registry = ModelRegistry()
    if run_id:
        items = model_invocation_repo.list_by_run(run_id, limit=limit, task_type=task_type)
    else:
        items = model_invocation_repo.list_recent(task_type=task_type, limit=limit)
    return {
        "items": items,
        "routing": {
            "unrecognized": registry.unrecognized(),
            "resolved": {
                task: {
                    "provider": spec.provider,
                    "model": spec.model,
                }
                for task in ("extract", "analyze", "risk", "narrative", "embedding", "vision")
                for spec in (registry.resolve(task).primary,)
            },
        },
    }


@router.get("/api/v1/ops/channels")
async def list_channel_status() -> dict[str, object]:
    """返回渠道配置状态，而非伪造外部平台实时连通性。"""
    configured = feishu_client.is_configured
    return {
        "items": [{
            "channel": "feishu",
            "status": "configured" if configured else "not_configured",
            "configured": configured,
            "inbound_endpoint": "/api/v1/channels/feishu/webhook",
        }],
    }
