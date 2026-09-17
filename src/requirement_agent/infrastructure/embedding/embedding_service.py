"""用于文本向量化的 embedding 服务适配器。"""

from __future__ import annotations

from requirement_agent.config.settings import settings
from requirement_agent.infrastructure.llm.openai_provider import LLMProvider


def current_embedding_model() -> str:
    """当前生效的 embedding 模型名 —— **写向量来源时一律用它**。

    不直接读 `settings.embedding_model`：那会忽略 `MODEL_ROUTES["embedding"]`，
    于是记下来的来源与实际用的模型对不上（**记错比不记更坏**）。
    统一走注册表，与 `EmbeddingService` 实际用的那个是同一个来源。
    """
    from requirement_agent.infrastructure.llm.model_registry import ModelRegistry

    return ModelRegistry().get_embedding_model().model


class EmbeddingDimensionError(RuntimeError):
    """返回向量的维度与 `EMBEDDING_DIMENSION` 不一致。

    继续写入的后果是二选一：被 `vector(N)` 列直接拒绝，或（凑巧同维时）写进一条
    检索不到的脏数据。两种都不该静默发生，所以在这一层就拦掉。
    """


class EmbeddingUnavailableError(RuntimeError):
    """没有可用的向量：网关未配置，或输入为空。

    这里以前返回等维全零占位「保证链路不崩」，实际后果是**假向量落库**：
    - pgvector 对零向量的余弦距离是 `NaN`（实测 `[0,0,0] <=> [1,2,3] = nan`），
      于是 `1 - NaN = NaN` 作为检索分一路传到前端，渲染成乱码百分比；
    - outbox 事件仍然被标成 `completed`，没有任何地方会察觉。

    改为抛错后行为从「静默写脏数据」变成「可见地失败」——所有调用方本来就捕获异常
    并降级：`_embed_safe` 返回 None、检索退回关键词、outbox 任务标记失败并重试/死信。
    """


class EmbeddingService:
    """负责将需求文本转换为向量表示，供检索和相似度比较使用。

    配置了 embedding 即返回真实向量；**未配置或输入为空则抛错**，绝不返回占位向量。
    """

    def __init__(self, provider: LLMProvider | None = None) -> None:
        if provider is not None:
            self.provider = provider
        else:
            # 走注册表：`MODEL_ROUTES["embedding"]` 配了才生效（B3.1）。
            # 没配时注册表返回全局 `EMBEDDING_MODEL` —— 与改造前一致。
            from requirement_agent.infrastructure.llm.model_registry import ModelRegistry

            self.provider = LLMProvider(
                spec=ModelRegistry().get_embedding_model(), task_type="embedding"
            )

    @property
    def model_name(self) -> str:
        """当前实际使用的 embedding 模型名 —— **写向量来源时以它为准**。"""
        return self.provider.embedding_model

    def embed(self, text: str) -> list[float]:
        """返回 embedding 向量（维度取 `embedding_dimension`）。

        - 输入为空、或完全未配置（既无独立 embedding 也无 chat provider）：抛
          `EmbeddingUnavailableError`；
        - 已配置但调用失败：异常向上抛，由调用方决定（outbox 重试 / 退化关键词）；
        - 维度与 `EMBEDDING_DIMENSION` 不符：抛 `EmbeddingDimensionError`。
        """
        if not text.strip():
            raise EmbeddingUnavailableError("空文本没有可嵌入的内容")
        if not self.is_configured():
            raise EmbeddingUnavailableError(
                "未配置 embedding 网关（EMBEDDING_BASE_URL + EMBEDDING_API_KEY）"
            )
        return self._validated(self.provider.embed(text))

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
