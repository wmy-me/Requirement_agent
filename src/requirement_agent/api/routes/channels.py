"""渠道 Webhook 路由（当前只有飞书事件订阅）。

`POST /api/v1/channels/feishu/webhook` 是本项目**唯一对外暴露且不经 HTTP 鉴权**的端点，
它的安全性完全依赖飞书自身的签名校验与 Verification Token。将来接入全局鉴权中间件
（阶段 6 的 RBAC）时，必须把本路径列入豁免名单，否则飞书将无法回调。

处理顺序（与官方文档一致）：
1. 解密 —— 配置 Encrypt Key 时请求体是 `{"encrypt": "..."}` 密文；
2. URL 校验回调 —— 文档明确其**不适用**签名校验（excluding request URL verification），
   故单独分支处理，但仍要求 Verification Token 匹配；
3. 其余请求 —— 签名校验 → token 校验；
4. 归一化 → 落库 + 入队，**不等分析结果**（飞书要求秒级应答，分析由后台消费补上）。

注意：本 router **不设 tags**，由父 router 在 include 时补齐。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, status

from requirement_agent.api.dependencies import channel_ingest_service, feishu_client
from requirement_agent.infrastructure.channels.feishu_client import FeishuPayloadError

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/v1/channels/feishu/webhook")
async def feishu_webhook(request: Request) -> dict[str, object]:
    """接收飞书事件回调：验签 → 解密 → 归一化 → 落库入队 → 立即返回。"""
    if not feishu_client.is_configured:
        # 未配置的渠道端点不接受任何输入
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="feishu channel is not configured",
        )

    raw_body = await request.body()
    try:
        payload = feishu_client.decode(raw_body)
    except FeishuPayloadError as exc:
        logger.warning("event=feishu_decode_failed error=%s", exc)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    # —— URL 校验：官方文档明确其不适用签名校验流程，故在验签之前处理 ——
    if feishu_client.is_challenge(payload):
        if not feishu_client.verify_token(payload):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid verification token"
            )
        challenge = payload.get("challenge")
        if not challenge:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="challenge is missing"
            )
        return {"challenge": challenge}

    if not feishu_client.verify(request.headers, raw_body):
        logger.warning("event=feishu_signature_rejected")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid signature")

    if not feishu_client.verify_token(payload):
        logger.warning("event=feishu_token_rejected")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid verification token"
        )

    inbound = feishu_client.parse(payload)
    result = channel_ingest_service.ingest(inbound)
    logger.info(
        "event=feishu_event_handled accepted=%s deduplicated=%s source_id=%s",
        result.get("accepted"),
        result.get("deduplicated"),
        result.get("source_id"),
    )
    return {"code": 0, "msg": "success", **result}
