"""用于文本向量化的 embedding 服务适配器。"""

from __future__ import annotations

from src.requirement_agent.config.settings import settings
from src.requirement_agent.infrastructure.llm.openai_provider import LLMProvider


class EmbeddingService:
    """负责将需求文本转换为向量表示，供检索和相似度比较使用。

    不再产出哈希伪向量：配置了 embedding 即返回真实向量；未配置时返回
    `EMBEDDING_DIMENSION` 维全零占位向量，并把“占位”意图留待调用方感知。
    """

    def __init__(self, provider: LLMProvider | None = None) -> None:
        self.provider = provider or LLMProvider()

    def embed(self, text: str) -> list[float]:
        """返回 embedding 向量（维度取 `embedding_dimension`）。

        - 已配置 embedding 网关：走真实模型，调用失败时**异常向上抛**，由 outbox 任务
          负责重试/死信，避免静默写入零向量或伪向量。
        - 完全未配置（既无独立 embedding 也无 chat provider）：返回等维全零占位，
          保证链路不至于崩掉——但此时调用方应感知到配置缺失。
        """
        if not text:
            return [0.0] * settings.embedding_dimension
        if self.is_configured():
            return self.provider.embed(text)
        return [0.0] * settings.embedding_dimension

    def is_configured(self) -> bool:
        """embedding 网关是否可用。

        以独立 embedding 配置为准（EMBEDDING_BASE_URL + EMBEDDING_API_KEY）；
        未配置独立 embedding 时，回退到 chat provider 是否就绪——此时 embed 会复用
        chat 的 base_url/key（不保证有 /embeddings 端点，可能 404）。
        """
        # embedding 以独立配置为准；未给独立 base_url/key 时，回退到 chat provider 是否就绪
        return self.provider.embedding_configured() or self.provider.is_configured()
