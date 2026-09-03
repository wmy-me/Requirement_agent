"""Feishu integration adapter for requirement ingestion."""

from __future__ import annotations

from typing import Any


class FeishuClient:
    """Processes webhook payloads from Feishu."""

    def __init__(self, app_id: str = "", app_secret: str = "") -> None:
        self.app_id = app_id
        self.app_secret = app_secret

    def parse_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        text = payload.get("text") or payload.get("content") or ""
        return {
            "source_type": "feishu",
            "requester_id": payload.get("user_id"),
            "requester_name": payload.get("user_name"),
            "original_text": str(text),
            "metadata": payload,
        }
