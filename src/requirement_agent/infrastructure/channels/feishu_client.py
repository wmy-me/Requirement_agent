"""Feishu integration adapter for requirement ingestion."""

from __future__ import annotations

from typing import Any


class FeishuClient:
    """Processes webhook payloads from Feishu."""

    def __init__(self, app_id: str = "", app_secret: str = "") -> None:
        self.app_id = app_id
        self.app_secret = app_secret

    def parse_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        """把飞书 Webhook 原始载荷归一化为可入库的 source 字段。

        （当前为未接线的 stub：返回的 `source_type='feishu'` 尚未被 schema 枚举接受，
        全项目也无调用方。待飞书接入时补签名校验 / event_id 幂等后启用。）
        """
        text = payload.get("text") or payload.get("content") or ""
        return {
            "source_type": "feishu",
            "requester_id": payload.get("user_id"),
            "requester_name": payload.get("user_name"),
            "original_text": str(text),
            "metadata": payload,
        }
