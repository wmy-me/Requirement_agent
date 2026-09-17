"""按任务的备用链执行模型调用（B3.1b）。

B3.1a 把「哪个任务用哪个模型」做成了配置，但**链是死的** ——
`get_fallback_chain()` 拿得到备用模型，却没有任何代码会去用它们。
这个模块把它接上：主模型失败时沿链换，并把「用到了第几级」如实带出去。

## 三条边界

**① 不是所有错误都换模型。** 分类见 `errors.py`：`invalid_request`（schema 写错、
密钥无效、模型名不存在）**既不重试也不换** —— 请求本身有问题，换谁都是一样的结果，
换只会把「代码里的 bug」伪装成「主模型不太行」。这条边界很要紧：
不加区分地换模型，会让本该立刻暴露的配置错误变成一次「降级后成功」。

**② 单模型链保持原样抛原始异常。** `MODEL_ROUTES` 默认是空的，此时链上只有主模型
（= 全局配置）。那种情况下**必须重抛原始异常**而不是包一层 —— 否则「引入 fallback」
就改变了默认配置下的错误类型，既有调用方与测试会莫名其妙地挂。

**③ 流式不做跨模型降级。** 追加文档 §4.2 的失败策略表里**没有 narrative**，
而且流式一旦开始吐字就没法换模型（两次输出拼在一起会前后不一致）。
叙述只是给人看的文字，失败时由 `_fallback_narrative` 兜成确定性总结 ——
这比「换个模型接着上一句往下说」诚实得多。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from requirement_agent.infrastructure.llm.errors import (
    classify_error,
    error_summary,
    is_fallback_worthy,
)
from requirement_agent.infrastructure.llm.model_registry import ModelRegistry, ModelSpec
from requirement_agent.infrastructure.llm.openai_provider import LLMProvider

logger = logging.getLogger(__name__)

__all__ = ["AllModelsFailedError", "ChatOutcome", "ModelRouter"]


@dataclass(frozen=True, slots=True)
class ChatOutcome:
    """一次成功的对话调用 —— 连同**它是从哪一级来的**。

    `fallback_level == 0` 表示就是主模型，没有任何降级。调用方据此决定要不要
    在业务结果上打「降级」标记（见 `errors.py` 与各 Skill）。
    """

    text: str
    spec: ModelSpec
    fallback_level: int
    fallback_from: str | None
    attempted: tuple[str, ...] = ()

    @property
    def degraded(self) -> bool:
        return self.fallback_level > 0


class AllModelsFailedError(RuntimeError):
    """备用链上的模型全试过了，都没成。

    **消息里带上每一级的错误** —— 「都失败了」等于没写；排障时要一眼看出
    「是同一类错误（说明是我们这边的问题）还是各级各不相同（说明是它们那边的问题）」。
    """

    def __init__(self, task_type: str, failures: list[tuple[str, str]]) -> None:
        detail = "；".join(f"{spec} → {summary}" for spec, summary in failures)
        super().__init__(f"任务 {task_type} 的主备模型全部失败（共 {len(failures)} 次）：{detail}")
        self.task_type = task_type
        self.failures = failures


class ModelRouter:
    """按 `task_type` 取模型链并执行，主模型失败时沿链降级。"""

    def __init__(self, registry: ModelRegistry | None = None) -> None:
        self.registry = registry or ModelRegistry()

    def has_any_configured(self, task_type: str) -> bool:
        """这条链上**有没有**任何一级是配了凭据的。

        调用方的「要不要走模型」前置判断用它，而不是只看主模型 ——
        主模型没配但备用配了，照样应该走模型（见 `BaseSkill.has_llm`）。
        """
        return any(
            LLMProvider(spec=spec).is_configured()
            for spec in self.registry.resolve(task_type).chain()
        )

    def generate(
        self, task_type: str, prompt: str, *, system_prompt: str | None = None
    ) -> ChatOutcome:
        """执行一次对话调用，返回文本**以及它来自哪一级模型**。

        失败处置按 `errors.classify_error` 的分类走（见模块 docstring ①）。
        """
        chain = self.registry.resolve(task_type).chain()
        primary_desc = _describe(chain[0])
        failures: list[tuple[str, str]] = []

        for level, spec in enumerate(chain):
            provider = LLMProvider(spec=spec, task_type=task_type)
            # 降级信息由 provider 在记录调用时读取（B3.1a 已把列与属性备好）
            provider.fallback_level = level
            provider.fallback_from = primary_desc if level > 0 else None

            # 没配凭据的模型**直接跳过**，不做「调一次、失败、再换」——
            # 它根本调不了，试它只是白等一个连接超时。跳过是**配置问题**的可见形式：
            # 每一级都跳完时会抛出下面那条明确的错误。
            if not provider.is_configured():
                logger.warning(
                    "event=llm_route_skip_unconfigured task_type=%s level=%d spec=%s",
                    task_type,
                    level,
                    _describe(spec),
                )
                failures.append((_describe(spec), "未配置凭据（provider 名或密钥缺失）"))
                continue

            try:
                text = provider.generate(prompt, system_prompt=system_prompt)
            except Exception as exc:  # noqa: BLE001 —— 分类后决定「换/不换/抛」
                kind = classify_error(exc)
                failures.append((_describe(spec), error_summary(exc)))
                last = level == len(chain) - 1
                if last or not is_fallback_worthy(kind):
                    logger.warning(
                        "event=llm_route_give_up task_type=%s level=%d kind=%s spec=%s reason=%s",
                        task_type,
                        level,
                        kind,
                        _describe(spec),
                        "链路已尽" if last else "该类别不值得换模型",
                    )
                    # ⚠️ 单模型链**重抛原始异常**（见模块 docstring ②）：
                    # 默认配置（MODEL_ROUTES 为空）下链上只有主模型，
                    # 包一层会让默认行为下的错误类型变掉。
                    if len(chain) == 1:
                        raise
                    raise AllModelsFailedError(task_type, failures) from exc
                logger.warning(
                    "event=llm_route_fallback task_type=%s level=%d kind=%s from=%s error=%s",
                    task_type,
                    level,
                    kind,
                    _describe(spec),
                    error_summary(exc),
                )
                continue
            if level > 0:
                logger.warning(
                    "event=llm_degraded task_type=%s level=%d model=%s fallback_from=%s",
                    task_type,
                    level,
                    _describe(spec),
                    primary_desc,
                )
            return ChatOutcome(
                text=text,
                spec=spec,
                fallback_level=level,
                fallback_from=primary_desc if level > 0 else None,
                attempted=tuple(_describe(spec) for spec in chain[: level + 1]),
            )

        # 所有模型要么没配凭据、要么失败过。
        if len(chain) == 1:
            # **单模型链保持与 B3.1 之前相同的错误类型**（见模块 docstring ②）：
            # 未配置时抛 RuntimeError，与 `BaseSkill._generate_json` 一直以来的判定一致。
            raise RuntimeError("LLM provider is not configured")
        raise AllModelsFailedError(task_type, failures or [("<空链>", "没有可用模型")])


def _describe(spec: ModelSpec) -> str:
    return f"{spec.provider}/{spec.model}" if spec.provider else "<未配置>"
