"""LLM 提示词技能的公共辅助逻辑。"""

from __future__ import annotations

import json
import re
from typing import Any

from requirement_agent.infrastructure.llm.invocation import bind_prompt_version
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

    def __init__(self, provider: LLMProvider | None = None, *, task_type: str = "") -> None:
        self.task_type = task_type or type(self).task_type
        self.provider = provider or self._resolve_provider()

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
        if not self.provider.is_configured():
            raise RuntimeError("LLM provider is not configured")

        # 绑 prompt 版本：provider 记调用时读它。
        # ⚠️ 由**技能层**绑而不是 provider 自己读 prompts 模块 —— provider 在
        # infrastructure 层，反向依赖 skills 会把分层搞反。
        with bind_prompt_version(PROMPT_VERSION):
            text = self.provider.generate(prompt, system_prompt=system_prompt)
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
