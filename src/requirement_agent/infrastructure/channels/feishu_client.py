"""飞书渠道适配器。

当前只做**载荷归一化**：把飞书事件订阅的 v2 结构解析成 `InboundRequirement`。

尚未实现（接线前必须补齐，否则等于对外裸奔）：
- URL 验证 challenge 的应答；
- 配置了 Encrypt Key 时的 AES 解密（密文是 `{"encrypt": "..."}` 信封）；
- `X-Lark-Signature` 等请求头的签名校验 —— 这是 `verify` 覆写的位置。
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from requirement_agent.infrastructure.channels.base import ChannelAdapter, InboundRequirement


class FeishuClient(ChannelAdapter):
    """飞书事件订阅适配器。"""

    channel = "feishu"

    def __init__(self, app_id: str = "", app_secret: str = "") -> None:
        self.app_id = app_id
        self.app_secret = app_secret

    def parse(self, payload: dict[str, Any]) -> InboundRequirement:
        """把飞书事件载荷归一化为 `InboundRequirement`。

        兼容两种结构：
        - v2.0 事件：`{"schema": "2.0", "header": {...}, "event": {"message": {...}, "sender": {...}}}`
        - 扁平结构：直接给 `text` / `content` 的简化载荷（内部联调与单测用）

        注意：飞书文本消息的 `message.content` 是一段 JSON 字符串（形如 `{"text":"..."}`），
        这里按 JSON 解一层；解不出来就按纯文本处理。@ 提及的清洗留待接线时补。
        """
        header = payload.get("header") or {}
        event = payload.get("event") or {}
        message = event.get("message") or {}
        sender = event.get("sender") or {}
        sender_id = sender.get("sender_id") or {}

        raw_text = (
            message.get("content")
            or event.get("text")
            or payload.get("text")
            or payload.get("content")
            or ""
        )
        text = raw_text
        if isinstance(raw_text, dict):
            text = raw_text.get("text") or ""
        elif isinstance(raw_text, str) and raw_text.strip().startswith("{"):
            try:
                decoded = json.loads(raw_text)
            except json.JSONDecodeError:
                decoded = None
            if isinstance(decoded, dict) and decoded.get("text"):
                text = str(decoded["text"])

        return InboundRequirement(
            channel=self.channel,
            text=str(text or ""),
            # v2 的 header.event_id 是幂等键来源（v1 事件放在 event.event_id）
            event_id=str(header.get("event_id") or event.get("event_id") or "") or None,
            requester_id=str(sender_id.get("open_id") or payload.get("user_id") or "") or None,
            requester_name=str(payload.get("user_name") or "") or None,
            payload=payload,
            metadata={
                "schema": payload.get("schema"),
                "event_type": header.get("event_type") or event.get("type"),
                "message_type": message.get("message_type"),
            },
        )

    def verify(self, headers: Mapping[str, str], raw_body: bytes) -> bool:
        """尚未实现签名校验：沿用基类默认放行。

        接线时须覆写为「比对 verification token + 校验 X-Lark-Signature（+ 必要时 AES 解密）」。
        """
        return super().verify(headers, raw_body)
