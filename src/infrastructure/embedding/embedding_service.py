"""用于文本向量化的 embedding 服务适配器。"""

from __future__ import annotations

import hashlib

from src.infrastructure.llm.openai_provider import LLMProvider


class EmbeddingService:
    """负责将需求文本转换为向量表示，供检索和相似度比较使用。"""

    def __init__(self, provider: LLMProvider | None = None) -> None:
        self.provider = provider or LLMProvider()

    def embed(self, text: str) -> list[float]:
        """返回文本的 1536 维向量。

        模型可用则走真实 embedding；不可用/失败时退化为基于文本哈希的确定性伪向量，
        保证检索不中断（伪向量仅用于占位，语义召回会退化为关键词）。
        """
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
