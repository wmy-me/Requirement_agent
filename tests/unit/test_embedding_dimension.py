"""EmbeddingService 的守卫：维度不符、网关未配置、输入为空都必须显式失败，不得静默写库。"""

import pytest

from requirement_agent.config.settings import settings
from requirement_agent.infrastructure.embedding.embedding_service import (
    EmbeddingDimensionError,
    EmbeddingService,
    EmbeddingUnavailableError,
)


class FakeProvider:
    """可注入固定向量的假 provider。"""

    def __init__(self, vector: list[float], *, configured: bool = True) -> None:
        self.vector = vector
        self.configured = configured

    def is_configured(self) -> bool:
        return self.configured

    def embedding_configured(self) -> bool:
        return self.configured

    def embed(self, text: str) -> list[float]:
        return list(self.vector)


def test_expected_dimension_passes_through() -> None:
    vector = [0.1] * settings.embedding_dimension
    service = EmbeddingService(provider=FakeProvider(vector))
    assert service.embed("文本") == vector


def test_mismatched_dimension_raises() -> None:
    # 1536 是 006 迁移前的旧维度，写进 vector(4096) 列会被数据库拒绝
    service = EmbeddingService(provider=FakeProvider([0.1] * 1536))
    with pytest.raises(EmbeddingDimensionError) as err:
        service.embed("文本")
    assert str(settings.embedding_dimension) in str(err.value)


def test_empty_text_raises_instead_of_returning_placeholder() -> None:
    """⚠️ 行为反转：此处原先断言「返回等维全零占位」。

    零向量不是「无损的降级」：pgvector 对它的余弦距离是 NaN（实测
    `[0,0,0] <=> [1,2,3] = nan`），会顺着检索一路变成界面上的乱码分数，
    而写入方（outbox）还会把这次事件标成 completed。现在改为抛错。
    """
    service = EmbeddingService(provider=FakeProvider([0.1]))

    with pytest.raises(EmbeddingUnavailableError):
        service.embed("")


def test_unconfigured_raises_instead_of_returning_placeholder() -> None:
    """同上：未配置网关时抛错，由调用方走各自的降级路径（而不是写零向量）。"""
    service = EmbeddingService(provider=FakeProvider([], configured=False))

    with pytest.raises(EmbeddingUnavailableError) as err:
        service.embed("文本")
    assert "EMBEDDING_BASE_URL" in str(err.value)
