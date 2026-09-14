"""用于文本向量化的 embedding 服务适配器。"""

from __future__ import annotations

import logging

from requirement_agent.config.settings import settings
from requirement_agent.infrastructure.llm.openai_provider import LLMProvider

logger = logging.getLogger(__name__)


class EmbeddingDimensionError(RuntimeError):
    """返回向量的维度与 `EMBEDDING_DIMENSION` 不一致。

    继续写入的后果是二选一：被 `vector(N)` 列直接拒绝，或（凑巧同维时）写进一条
    检索不到的脏数据。两种都不该静默发生，所以在这一层就拦掉。
    """


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
            return self._validated(self.provider.embed(text))
        # 未配置时仍返回等维占位以保证链路不崩，但不再静默：占位向量检索无意义，
        # 必须有痕迹可查（容器部署曾因漏传 EMBEDDING_* 长期走这条路而不自知）。
        logger.warning(
            "event=embedding_placeholder reason=unconfigured dimension=%d "
            "（未配置 embedding 网关，写入的是全零占位向量，检索无意义）",
            settings.embedding_dimension,
        )
        return [0.0] * settings.embedding_dimension

    @staticmethod
    def _validated(vector: list[float]) -> list[float]:
        """校验维度与 `EMBEDDING_DIMENSION` 一致，不一致直接抛错。"""
        expected = settings.embedding_dimension
        if len(vector) != expected:
            raise EmbeddingDimensionError(
                f"embedding 维度 {len(vector)} 与 EMBEDDING_DIMENSION={expected} 不一致；"
                "拒绝写入以避免污染检索（请核对所用模型与 006 迁移后的向量列维度）"
            )
        return vector

    def is_configured(self) -> bool:
        """embedding 网关是否可用。

        以独立 embedding 配置为准（EMBEDDING_BASE_URL + EMBEDDING_API_KEY）；
        未配置独立 embedding 时，回退到 chat provider 是否就绪——此时 embed 会复用
        chat 的 base_url/key（不保证有 /embeddings 端点，可能 404）。
        """
        # embedding 以独立配置为准；未给独立 base_url/key 时，回退到 chat provider 是否就绪
        return self.provider.embedding_configured() or self.provider.is_configured()
