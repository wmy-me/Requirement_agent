"""LLM provider adapter for OpenAI-compatible APIs."""

from __future__ import annotations

from typing import Any

import httpx

from src.config.settings import settings


class LLMProvider:
    """Thin wrapper around an OpenAI-compatible chat and embedding API."""

    def __init__(self) -> None:
        self.provider_name = settings.llm_provider
        self.api_key = settings.active_llm_api_key
        self.base_url = settings.active_llm_base_url.rstrip("/")
        self.model = settings.active_llm_model
        self.embedding_model = settings.embedding_model

    def is_configured(self) -> bool:
        return bool(self.api_key) and bool(self.base_url)

    def generate(self, prompt: str, *, system_prompt: str | None = None) -> str:
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

    def embed(self, text: str) -> list[float]:
        if not self.is_configured():
            return [0.0] * 1536

        payload = {"model": self.embedding_model, "input": text}
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        response = httpx.post(
            f"{self.base_url}/embeddings",
            headers=headers,
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        body = response.json()
        values = body["data"][0]["embedding"]
        return [float(value) for value in values]
