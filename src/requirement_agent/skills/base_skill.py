"""LLM 提示词技能的公共辅助逻辑。"""

from __future__ import annotations

import json
import re
from typing import Any

from requirement_agent.infrastructure.llm.openai_provider import LLMProvider


class BaseSkill:
    """LLM provider 的通用包装层。

    如果项目未配置真实大模型 API key，技能会自动回退到原有的启发式规则逻辑，
    保证 Agent 在无模型环境下也能继续工作。
    """

    def __init__(self, provider: LLMProvider | None = None) -> None:
        self.provider = provider or LLMProvider()

    def _generate_json(self, prompt: str, system_prompt: str) -> dict[str, Any]:
        if not self.provider.is_configured():
            raise RuntimeError("LLM provider is not configured")

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
