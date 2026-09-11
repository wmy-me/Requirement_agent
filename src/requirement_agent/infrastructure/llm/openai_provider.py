"""兼容 OpenAI 接口格式的 LLM provider 适配器。"""

from __future__ import annotations

import json
from typing import Any, Iterator

import httpx

from requirement_agent.config.settings import settings


class LLMProvider:
    """对兼容 OpenAI 协议的 chat 与 embedding API 的轻量封装。"""

    def __init__(self) -> None:
        self.provider_name = settings.llm_provider
        self.api_key = settings.active_llm_api_key
        self.base_url = settings.active_llm_base_url.rstrip("/")
        self.model = settings.active_llm_model
        self.embedding_model = settings.embedding_model

    def is_configured(self) -> bool:
        """当前激活 provider（openai/deepseek）是否具备 key 与 base_url。"""
        return bool(self.api_key) and bool(self.base_url)

    def embedding_configured(self) -> bool:
        """embedding 是否具备独立 base_url 与 key（优先于 chat provider 的 embedding 能力）。"""
        return bool(settings.embedding_base_url.strip()) and bool(settings.embedding_api_key.get_secret_value().strip())

    def _embedding_base_url(self) -> str:
        """embedding 请求的 base_url：独立配置优先，否则复用 chat provider 的 base_url。"""
        return settings.embedding_base_url.strip().rstrip("/") or self.base_url or ""

    def _embedding_api_key(self) -> str:
        """embedding 请求的 api key：独立配置优先，否则复用 chat provider 的 key。"""
        return settings.embedding_api_key.get_secret_value().strip() or self.api_key

    def generate(self, prompt: str, *, system_prompt: str | None = None) -> str:
        """非流式补全：返回完整生成文本。未配置时返回提示文案（由上层决定是否回退）。"""
        if not self.is_configured():
            return f"{self.provider_name} is not configured. Set API key and base URL first."

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [],
        }
        if system_prompt:
            payload["messages"].append({"role": "system", "content": system_prompt})
        payload["messages"].append({"role": "user", "content": prompt})

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        response = httpx.post(
            f"{self.base_url}/chat/completions",
            headers=headers,
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        body = response.json()
        return body["choices"][0]["message"]["content"]

    def generate_stream(self, prompt: str, *, system_prompt: str | None = None) -> Iterator[str]:
        """以流式方式生成 chat 补全，逐段产出增量文本。"""
        if not self.is_configured():
            yield f"{self.provider_name} is not configured. Set API key and base URL first."
            return

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [],
            "stream": True,
        }
        if system_prompt:
            payload["messages"].append({"role": "system", "content": system_prompt})
        payload["messages"].append({"role": "user", "content": prompt})

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        with httpx.stream(
            "POST",
            f"{self.base_url}/chat/completions",
            headers=headers,
            json=payload,
            timeout=httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0),
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line:
                    continue
                if line.startswith("data:"):
                    data = line[len("data:"):].strip()
                else:
                    data = line.strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                content = delta.get("content")
                if content:
                    yield content

    def embed(self, text: str) -> list[float]:
        """调用 embedding 模型返回向量；unconfigured 时返回 1536 维全零占位。

        embedding 走独立配置（EMBEDDING_BASE_URL/EMBEDDING_API_KEY/EMBEDDING_MODEL），
        与 chat provider 解耦——避免 chat 是 deepseek（无 /embeddings 端点）时 404。
        """
        if not (self.embedding_configured() or self.is_configured()):
            return [0.0] * settings.embedding_dimension

        payload = {"model": self.embedding_model, "input": text}
        headers = {
            "Authorization": f"Bearer {self._embedding_api_key()}",
            "Content-Type": "application/json",
        }
        response = httpx.post(
            f"{self._embedding_base_url()}/embeddings",
            headers=headers,
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        body = response.json()
        values = body["data"][0]["embedding"]
        return [float(value) for value in values]
