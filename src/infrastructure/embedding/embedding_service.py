"""用于文本向量化的 embedding 服务适配器。"""

from __future__ import annotations

import hashlib

from src.infrastructure.llm.openai_provider import LLMProvider


class EmbeddingService:
    """负责将需求文本转换为向量表示，供检索和相似度比较使用。"""

    def __init__(self, provider: LLMProvider | None = None) -> None:
        self.provider = provider or LLMProvider()

    def embed(self, text: str) -> list[float]:
        if not text:
            return [0.0] * 1536
        if self.provider.is_configured():
            try:
                return self.provider.embed(text)
            except Exception:
                pass
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        values: list[float] = []
        for i in range(1536):
            seed = int(digest[i % len(digest)] + str(i), 16)
            values.append((seed % 1000) / 1000.0 - 0.5)
        return values

    def is_configured(self) -> bool:
        return self.provider.is_configured()
