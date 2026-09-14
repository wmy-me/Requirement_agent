"""渠道适配器的公共抽象。

分工约定：每个渠道只实现两件事 —— 把该渠道的原始事件**归一化**成
`InboundRequirement`（`parse`），以及在需要时做渠道自身的请求校验（`verify`）。
落库、幂等去重、异步分析都由上层 `ChannelIngestService` 统一处理，渠道实现不碰这些。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(slots=True)
class InboundRequirement:
    """一条已归一化的入站需求（渠道无关）。"""

    channel: str
    text: str
    # 渠道侧的事件 ID：幂等去重的依据，同一 (channel, event_id) 只入库一次
    event_id: str | None = None
    requester_id: str | None = None
    requester_name: str | None = None
    # 原始载荷留档，便于追溯渠道到底送来了什么
    payload: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_usable(self) -> bool:
        """正文为空的事件没有入库价值。

        渠道会推送大量非需求类事件（菜单点击、成员变更等），过滤在这一层做，
        避免它们占满来源表。
        """
        return bool(self.text.strip())


class ChannelAdapter(ABC):
    """渠道适配器基类。

    子类必须实现 `parse`；`verify` 默认放行，供需要签名/解密校验的渠道覆写。
    """

    channel: str = ""

    @abstractmethod
    def parse(self, payload: dict[str, Any]) -> InboundRequirement:
        """把渠道原始载荷归一化为 `InboundRequirement`。"""

    def verify(self, headers: Mapping[str, str], raw_body: bytes) -> bool:
        """校验请求来源合法性，默认放行。

        需要验签/解密的渠道（如飞书）覆写本方法；返回 False 表示拒绝该请求。
        """
        return True
