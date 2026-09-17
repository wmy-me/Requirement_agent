"""LLM 提示词技能的公共辅助逻辑。"""

from __future__ import annotations

import json
import re
from typing import Any

from requirement_agent.infrastructure.llm.invocation import (
    bind_prompt_version,
    last_route_outcome,
    set_last_route_outcome,
)
from requirement_agent.infrastructure.llm.router import ModelRouter
from requirement_agent.infrastructure.llm.openai_provider import LLMProvider
from requirement_agent.skills.prompts import PROMPT_VERSION


class BaseSkill:
    """LLM provider 的通用包装层。

    如果项目未配置真实大模型 API key，技能会自动回退到原有的启发式规则逻辑，
    保证 Agent 在无模型环境下也能继续工作。

    ## `task_type`（B3.1）

    传了它，**没显式给 provider 时**就按它在 `ModelRegistry` 里选模型
    —— 「哪个任务用哪个模型」的配置因此只需要改环境变量，不用改代码。

    不传时行为与 B3.1 之前**逐字节一致**（无参 `LLMProvider()`，走全局配置）。
    这样既有的调用点与测试完全不受影响。
    """

    #: 子类覆盖它来声明自己属于哪个任务（见 `model_registry.TASK_TYPES`）。
    task_type: str = ""

    def __init__(
        self,
        provider: LLMProvider | None = None,
        *,
        task_type: str = "",
        router: ModelRouter | None = None,
    ) -> None:
        self.task_type = task_type or type(self).task_type

        # ⚠️ **显式注入 provider = 调用方明确知道自己要什么，不该再被路由。**
        #
        # 这条不是设计洁癖，是实施时踩出来的：初版 `_generate_json` 无条件走
        # `ModelRouter()`，而路由器**自建** provider —— 于是 `self.provider`
        # 这个注入点被整个架空，11 条注入假 provider 的测试直接打到真 LLM 上。
        #
        # 生产里也只有「没给 provider」时才需要路由（`dependencies.py` 给的
        # `provider=` 是测试与特殊装配用的逃生口）。
        self._routed = provider is None
        self.provider = provider or self._resolve_provider()
        # ⚠️ `router or ModelRouter()` —— **不能只写 `router`**。
        # 初版写成 `router if self._routed else None`，而不传 router 时它就是 None，
        # 于是 `_generate_json` 永远走「不路由」那条分支 —— 整个 B3.1b 静默失效，
        # 而 `_routed=True` 看着像接上了。实跑 e2e 才发现（`analysis.degraded` 莫名是 True）。
        self.router = (router or ModelRouter()) if self._routed else None

    def has_llm(self) -> bool:
        """**整条链上**有没有可用的模型。

        ⚠️ 不能用 `self.provider.is_configured()` 代替：那只看**主模型**。
        实测撞到过——`risk` 的主模型配成了没凭据的 openai，于是前置检查直接把它
        判成「没有模型」，退回启发式，**备用链根本没机会**。
        有备用却不用，与「配了路由却没生效」是同一类失望。
        """
        if self.router is not None:
            return self.router.has_any_configured(self.task_type)
        return self.provider.is_configured()

    def _resolve_provider(self) -> LLMProvider:
        """没显式给 provider 时：按 task_type 路由；没有 task_type 则沿用全局配置。"""
        if not self.task_type:
            return LLMProvider()
        from requirement_agent.infrastructure.llm.model_registry import ModelRegistry

        return LLMProvider(
            spec=ModelRegistry().get_chat_model(self.task_type),
            task_type=self.task_type,
        )

    def _generate_json(self, prompt: str, system_prompt: str) -> dict[str, Any]:
        # ⚠️ 用 `has_llm()` 而不是 `self.provider.is_configured()` —— 后者只看主模型，
        # 会在**到达路由器之前**就抛出去，于是「主模型没配但有备用」的链根本没机会跑。
        # 实测撞到过：risk 的主模型配成没凭据的 openai，这里直接抛，
        # 备用链形同虚设（而报错还写着「provider is not configured」，看不出是主模型的问题）。
        if not self.has_llm():
            raise RuntimeError("LLM provider is not configured")

        # 每次调用先清空「上次路由结果」—— **清空是必须的**：
        # 上一次的成功结果留在这里，会让这次失败时被误读成「降级成了备用模型」。
        set_last_route_outcome(None)

        # 绑 prompt 版本：provider 记调用时读它。
        # ⚠️ 由**技能层**绑而不是 provider 自己读 prompts 模块 —— provider 在
        # infrastructure 层，反向依赖 skills 会把分层搞反。
        with bind_prompt_version(PROMPT_VERSION):
            if self.router is None:
                # 显式注入的 provider：直接调，不路由（见 __init__ 的说明）
                text = self.provider.generate(prompt, system_prompt=system_prompt)
            else:
                # 走路由：主模型失败时沿备用链换（B3.1b）。
                # ⚠️ 链上只有主模型时（默认配置），路由器**重抛原始异常** ——
                # 默认行为下的错误类型不变。
                outcome = (self.router or ModelRouter()).generate(
                    self.task_type, prompt, system_prompt=system_prompt
                )
                text = outcome.text
                set_last_route_outcome(
                    {
                        "degraded": outcome.degraded,
                        "source": "fallback_model" if outcome.degraded else "primary",
                        "reason": (
                            f"主模型 {outcome.fallback_from} 失败，已降级到 "
                            f"{outcome.spec.provider}/{outcome.spec.model}"
                            if outcome.degraded
                            else ""
                        ),
                        "model": f"{outcome.spec.provider}/{outcome.spec.model}",
                    }
                )
        candidate = self._extract_json_block(text)
        if not isinstance(candidate, dict):
            raise ValueError("LLM response was not a JSON object")
        return candidate

    @staticmethod
    def _extract_json_block(text: str) -> Any:
        cleaned = text.strip()
        code_block = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.DOTALL)
        if code_block:
            return json.loads(code_block.group(1))

        json_start = cleaned.find("{")
        json_end = cleaned.rfind("}")
        if json_start != -1 and json_end > json_start:
            return json.loads(cleaned[json_start : json_end + 1])

        raise ValueError("No JSON object found in the LLM response")


def degradation_marker() -> tuple[bool, str | None]:
    """本次调用的降级状态 → `(degraded, reason)`。

    **三条路径共用这一个函数**（调用了备用模型 / 模型失败退回规则 / 压根没配模型）——
    散在各技能里各写一遍，迟早有一条被漏掉，而漏掉的那条恰好是「静默当作确定结果」。

    - 路由结果里 `degraded=True` → 主模型失败、走了备用模型
    - 路由结果读不到（`None`）→ **这次调用没成功**，调用方正在走启发式分支
    - 压根没进过路由（未配模型）→ 也读不到，同样按降级处理
    """
    outcome = last_route_outcome()
    if not outcome:
        return True, "模型不可用，结论来自启发式规则"
    if outcome.get("degraded"):
        return True, str(outcome.get("reason") or "主模型失败，已降级到备用模型")
    return False, None
