"""Feishu webhook adapter stub."""

from __future__ import annotations


class FeishuClient:
    """Processes webhook payloads from Feishu."""

    def parse_event(self, payload: dict[str, object]) -> dict[str, object]:
        return {"source_type": "feishu", "payload": payload}
