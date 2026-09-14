"""EmbeddingService 的维度守卫：维度不符必须显式失败，不得静默写库。"""

import logging

import pytest

from requirement_agent.config.settings import settings
from requirement_agent.infrastructure.embedding.embedding_service import (
    EmbeddingDimensionError,
    EmbeddingService,
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


def test_empty_text_returns_placeholder_of_expected_dimension() -> None:
    service = EmbeddingService(provider=FakeProvider([0.1], configured=False))
    assert len(service.embed("")) == settings.embedding_dimension


def test_unconfigured_returns_placeholder_but_warns(caplog: pytest.LogCaptureFixture) -> None:
    service = EmbeddingService(provider=FakeProvider([], configured=False))
    with caplog.at_level(logging.WARNING):
        vector = service.embed("文本")
    assert len(vector) == settings.embedding_dimension
    assert any("embedding_placeholder" in record.message for record in caplog.records)
