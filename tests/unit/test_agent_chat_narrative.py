"""对话叙事链路的回归测试。

背景：`_narrative_chunks` 里写成 `except queue.Empty`，但本模块是
`from queue import Queue` —— **没有导入 queue 模块**，而局部变量 `queue` 会遮蔽模块名。
于是第一次 `get_nowait()` 抛 `Empty`（执行器线程还没来得及产出，几乎立刻发生）时，
`except` 子句**自己**抛 AttributeError，被管线的 `except Exception: pass` 吞掉，
`narrative_parts` 为空 → 永远退回规则文案。

实测后果：库里 9 条 assistant 消息中 8 条是模板拼的，模型叙事一次都没成功过。
"""

import asyncio

from requirement_agent.api.routes import agent_chat


class FakeStreamProvider:
    """配置为真、流式返回固定片段的假 provider。"""

    def __init__(self, pieces: list[str]) -> None:
        self.pieces = pieces

    def is_configured(self) -> bool:
        return True

    def generate_stream(self, prompt: str, system_prompt: str | None = None):
        yield from self.pieces


def _collect(pieces: list[str], monkeypatch) -> list[str]:
    monkeypatch.setattr(agent_chat, "LLMProvider", lambda: FakeStreamProvider(pieces))
    pipeline = {
        "extracted": {"requirement_title": "登录增强"},
        "candidates": [],
        "analysis": {"duplicate": False, "related": False, "conflict": False},
        "risk": {"quality_risk": "low", "change_risk": "low", "technical_impact_risk": "low"},
    }

    async def run() -> list[str]:
        return [token async for token in agent_chat._narrative_chunks(pipeline)]

    return asyncio.run(run())


def test_streaming_narrative_is_forwarded(monkeypatch) -> None:
    """核心回归：模型流必须被逐段转发，而不是抛错后落到规则文案。"""
    assert _collect(["这是", "模型叙事"], monkeypatch) == ["这是", "模型叙事"]


def test_streaming_narrative_handles_empty_stream(monkeypatch) -> None:
    """模型一段都没给时要正常结束，不能挂死（也不能抛错）。"""
    assert _collect([], monkeypatch) == []
