"""兼容 OpenAI 接口格式的 LLM provider 适配器。

请求调参（temperature / 超时 / 重试）统一取自 settings，调用点无需关心；
所有 httpx 调用收口在本类的 `_http_*` 方法，便于测试替换为假实现。
每次调用都会打一行 key=value 日志并累加进程内计量，供 `GET /api/v1/health/llm` 查看。
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from typing import Any, NamedTuple

import httpx

from requirement_agent.config.settings import settings
from requirement_agent.infrastructure.llm.invocation import (
    current_prompt_version,
    current_run_id,
    record_invocation,
)
from requirement_agent.infrastructure.llm.model_registry import ModelSpec

logger = logging.getLogger(__name__)

# 值得重试的状态码：限流与各类临时性服务端故障。
# 401/403/404/422 等属于请求本身的问题，重试没有意义，立即抛出。
_RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})

# `_observe` 的 `op` → task_type 的兜底映射。只在构造 provider 时**没给** task_type
# 时用得上（比如无参构造的老调用点）。给了 task_type 的一律以它为准 ——
# 那才是权威来源（同一个 `op="chat"` 可以是 extract 也可以是 risk）。
_OP_TO_TASK = {"chat": "unknown_chat", "stream": "unknown_stream", "embed": "embedding"}


class _CallResult(NamedTuple):
    """一次请求的返回值与计量信息。"""

    response: httpx.Response
    attempts: int
    duration_ms: float


# 进程内 LLM 调用计量。单实例部署（本项目当前形态）够用；
# 多实例或需要历史趋势时应换成真正的指标系统。
_LLM_CALL_STATS: dict[str, float] = {
    "calls_total": 0.0,
    "failures_total": 0.0,
    "retries_total": 0.0,
    "prompt_tokens_total": 0.0,
    "completion_tokens_total": 0.0,
    "duration_ms_total": 0.0,
}
_STATS_LOCK = threading.Lock()


def llm_call_stats() -> dict[str, float]:
    """LLM 调用计量快照（副本：调用方改动不会影响内部状态）。"""
    with _STATS_LOCK:
        return dict(_LLM_CALL_STATS)


def _elapsed_ms(started_at: float) -> float:
    return (time.perf_counter() - started_at) * 1000.0


class LLMProvider:
    """对兼容 OpenAI 协议的 chat 与 embedding API 的轻量封装。"""

    def __init__(self, *, spec: ModelSpec | None = None, task_type: str = "") -> None:
        """`spec` 给定时按它选网关与模型；不给时**逐字节沿用 B3.1 之前的行为**
        （全局 `LLM_PROVIDER` + `active_llm_*`）。

        为什么保留无参构造：全仓 6 处调用点与 19 条 provider 测试都用 `LLMProvider()`。
        路由是**增量能力**，不该强迫每一处都改 —— 只有明确知道自己属于哪个
        task_type 的调用点（技能层）才需要传 spec。
        """
        self.spec = spec
        self.task_type = task_type
        if spec is None:
            self.provider_name = settings.llm_provider
            self.api_key = settings.active_llm_api_key
            self.base_url = settings.active_llm_base_url.rstrip("/")
            self.model = settings.active_llm_model
        else:
            self.provider_name = spec.provider
            self.api_key = self._api_key_for(spec.provider)
            self.base_url = self._base_url_for(spec.provider).rstrip("/")
            self.model = spec.model
        # 降级信息（B3.1b 会真正使用；B3.1a 先占好位，让记录的形状一次到位，
        # 免得 B3.1b 再改一次表的写入）。level 0 = 主模型。
        self.fallback_level = 0
        self.fallback_from: str | None = None
        self.embedding_model = settings.embedding_model
        self.temperature = settings.llm_temperature
        self.timeout = settings.llm_timeout_seconds
        self.stream_read_timeout = settings.llm_stream_read_timeout_seconds
        self.max_retries = settings.llm_max_retries
        self.retry_backoff = settings.llm_retry_backoff_seconds

    @staticmethod
    def _api_key_for(provider: str) -> str:
        """按 provider 名取密钥。**不认识的名字返回空串**（而不是抛）——
        于是 `is_configured()` 为 False，调用方走既有的「未配置 → 启发式」分支。
        拼错 provider 名的表现与「没配模型」一致，而不是崩溃。"""
        if provider == "deepseek":
            return settings.deepseek_api_key.get_secret_value()
        if provider == "openai":
            return settings.openai_api_key.get_secret_value()
        return ""

    @staticmethod
    def _base_url_for(provider: str) -> str:
        if provider == "deepseek":
            return settings.deepseek_base_url
        if provider == "openai":
            return settings.openai_base_url
        return ""

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

    # ── 请求构造与传输 ──────────────────────────────────────────────────
    # 抽成独立方法是为了让测试能直接替换，既不碰真实网络也不真的等待。

    def _chat_payload(
        self, prompt: str, system_prompt: str | None, *, stream: bool = False
    ) -> dict[str, Any]:
        """统一的 chat 请求体；temperature 未配置时不写入该字段。"""
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {"model": self.model, "messages": messages}
        if stream:
            payload["stream"] = True
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        return payload

    @staticmethod
    def _headers(api_key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    def _http_post(
        self, url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float
    ) -> httpx.Response:
        """非流式 POST（generate / embed 共用）。"""
        return httpx.post(url, headers=headers, json=payload, timeout=timeout)

    def _http_stream(
        self, url: str, payload: dict[str, Any], headers: dict[str, str], timeout: httpx.Timeout
    ) -> AbstractContextManager[httpx.Response]:
        """流式 POST：返回上下文管理器，进入后才真正发起请求。"""
        return httpx.stream("POST", url, headers=headers, json=payload, timeout=timeout)

    def _sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def _backoff_seconds(self, attempt: int) -> float:
        """指数退避 + 抖动：抖动避免多实例在同一时刻一起重试形成尖峰。"""
        return self.retry_backoff * (2**attempt) * (0.5 + random.random() / 2)

    def _observe(
        self,
        *,
        op: str,
        model: str,
        outcome: str,
        duration_ms: float,
        attempts: int,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        error: BaseException | None = None,
        embedding_dimension: int | None = None,
    ) -> None:
        """记录一次 LLM 调用的计量：进程内计数 + 日志 + **可落库的调用记录**。

        ⚠️ 这里是**所有** LLM 调用的唯一收口点（7 个调用点全经过它），所以
        「记一次调用」只需要挂在这里一处 —— 散到每个调用点去记迟早漏一个。
        """
        with _STATS_LOCK:
            _LLM_CALL_STATS["calls_total"] += 1
            _LLM_CALL_STATS["duration_ms_total"] += duration_ms
            _LLM_CALL_STATS["retries_total"] += max(0, attempts - 1)
            _LLM_CALL_STATS["prompt_tokens_total"] += prompt_tokens
            _LLM_CALL_STATS["completion_tokens_total"] += completion_tokens
            if outcome != "ok":
                _LLM_CALL_STATS["failures_total"] += 1
        log = logger.info if outcome == "ok" else logger.warning
        log(
            "event=llm_call op=%s provider=%s model=%s task_type=%s outcome=%s attempts=%d "
            "duration_ms=%.1f prompt_tokens=%d completion_tokens=%d",
            op,
            self.provider_name,
            model,
            self.task_type or "-",
            outcome,
            attempts,
            duration_ms,
            prompt_tokens,
            completion_tokens,
        )
        record_invocation(
            {
                "run_id": current_run_id(),
                "task_type": self.task_type or _OP_TO_TASK.get(op, op),
                "provider": self.provider_name,
                "model": model,
                "prompt_version": current_prompt_version(),
                "input_tokens": prompt_tokens or None,
                "output_tokens": completion_tokens or None,
                "latency_ms": int(duration_ms),
                "status": outcome,
                # 错误分类在这里只到「有错/没」，细类留到 B3.1b 的错误分类
                "error_code": type(error).__name__ if error is not None else None,
                "fallback_level": self.fallback_level,
                "fallback_used": self.fallback_level > 0,
                "fallback_from": self.fallback_from,
                "embedding_dimension": embedding_dimension,
                # embedding 的「版本」= 模型名 + 维度：同名模型在不同部署上维度可能不同，
                # 而判断「有没有把新旧向量混在一起」必须靠这两者的组合。
                "embedding_version": (
                    f"{model}:{embedding_dimension}" if embedding_dimension else None
                ),
            }
        )

    @staticmethod
    def _usage_tokens(body: dict[str, Any] | None) -> tuple[int, int]:
        """从响应体的 usage 段取 (prompt_tokens, completion_tokens)；缺失一律按 0 计。"""
        usage = (body or {}).get("usage") or {}
        try:
            return int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
        except (TypeError, ValueError):
            return 0, 0

    def _request_with_retry(
        self, send: Callable[[], httpx.Response], *, op: str, model: str
    ) -> _CallResult:
        """执行请求，对可恢复错误做指数退避重试。

        失败路径在此处记账并抛错；成功路径由调用方拿到 usage 后自行记账
        （token 数只有解析完响应体才知道）。
        """
        started_at = time.perf_counter()
        attempts = 0
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            attempts += 1
            try:
                response = send()
                response.raise_for_status()
                return _CallResult(response, attempts, _elapsed_ms(started_at))
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in _RETRYABLE_STATUS:
                    # 请求本身有问题，重试无意义，立刻记账并抛出
                    self._observe(
                        op=op,
                        model=model,
                        outcome="error",
                        duration_ms=_elapsed_ms(started_at),
                        attempts=attempts,
                        error=exc,
                    )
                    raise
                last_error = exc
            except httpx.TransportError as exc:  # 含超时、连接失败等网络层异常
                last_error = exc
            if attempt < self.max_retries:
                self._sleep(self._backoff_seconds(attempt))
        assert last_error is not None, "重试循环至少执行一次，必已记录异常"
        self._observe(
            op=op,
            model=model,
            outcome="error",
            duration_ms=_elapsed_ms(started_at),
            attempts=attempts,
            error=last_error,
        )
        raise last_error

    @staticmethod
    def _iter_content(
        response: httpx.Response, usage_out: dict[str, Any] | None = None
    ) -> Iterator[str]:
        """解析 SSE 增量行，产出文本增量；空行、非 data 行与空 delta 一律跳过。

        网关若在流里回传 usage（部分实现会），顺带收集到 `usage_out`；
        不主动要求 usage，以免给不支持的网关塞未知字段。
        """
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
            # usage 可能出现在 choices 为空的收尾块里，故先于 choices 判断收集
            if usage_out is not None and chunk.get("usage"):
                usage_out.update(chunk["usage"])
            choices = chunk.get("choices") or []
            if not choices:
                continue
            content = (choices[0].get("delta") or {}).get("content")
            if content:
                yield content

    # ── 对外能力 ────────────────────────────────────────────────────────

    def generate(self, prompt: str, *, system_prompt: str | None = None) -> str:
        """非流式补全：返回完整生成文本。未配置时返回提示文案（由上层决定是否回退）。"""
        if not self.is_configured():
            return f"{self.provider_name} is not configured. Set API key and base URL first."

        payload = self._chat_payload(prompt, system_prompt)
        result = self._request_with_retry(
            lambda: self._http_post(
                f"{self.base_url}/chat/completions",
                payload,
                self._headers(self.api_key),
                self.timeout,
            ),
            op="chat",
            model=self.model,
        )
        body = result.response.json()
        prompt_tokens, completion_tokens = self._usage_tokens(body)
        self._observe(
            op="chat",
            model=self.model,
            outcome="ok",
            duration_ms=result.duration_ms,
            attempts=result.attempts,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        return body["choices"][0]["message"]["content"]

    def generate_stream(self, prompt: str, *, system_prompt: str | None = None) -> Iterator[str]:
        """以流式方式生成 chat 补全，逐段产出增量文本。

        重试只发生在**产出首个增量之前**（连接失败 / 可重试状态码）：一旦已经开始
        yield，重试会让调用方收到重复的半截内容，所以此时直接把异常抛出去。
        """
        if not self.is_configured():
            yield f"{self.provider_name} is not configured. Set API key and base URL first."
            return

        payload = self._chat_payload(prompt, system_prompt, stream=True)
        url = f"{self.base_url}/chat/completions"
        headers = self._headers(self.api_key)
        timeout = httpx.Timeout(connect=10.0, read=self.stream_read_timeout, write=30.0, pool=10.0)

        started_at = time.perf_counter()
        attempts = 0
        usage: dict[str, Any] = {}
        started = False
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            attempts += 1
            try:
                with self._http_stream(url, payload, headers, timeout) as response:
                    response.raise_for_status()
                    for piece in self._iter_content(response, usage):
                        started = True
                        yield piece
                prompt_tokens, completion_tokens = self._usage_tokens({"usage": usage})
                self._observe(
                    op="stream",
                    model=self.model,
                    outcome="ok",
                    duration_ms=_elapsed_ms(started_at),
                    attempts=attempts,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                )
                return
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in _RETRYABLE_STATUS:
                    self._observe(
                        op="stream",
                        model=self.model,
                        outcome="error",
                        duration_ms=_elapsed_ms(started_at),
                        attempts=attempts,
                        error=exc,
                    )
                    raise
                last_error = exc
            except httpx.TransportError as exc:
                last_error = exc
            if started or attempt >= self.max_retries:
                break
            self._sleep(self._backoff_seconds(attempt))
        assert last_error is not None, "重试循环至少执行一次，必已记录异常"
        self._observe(
            op="stream",
            model=self.model,
            outcome="error",
            duration_ms=_elapsed_ms(started_at),
            attempts=attempts,
            error=last_error,
        )
        raise last_error

    def embed(self, text: str) -> list[float]:
        """调用 embedding 模型返回向量；unconfigured 时返回全零占位。

        embedding 走独立配置（EMBEDDING_BASE_URL/EMBEDDING_API_KEY/EMBEDDING_MODEL），
        与 chat provider 解耦——避免 chat 是 deepseek（无 /embeddings 端点）时 404。
        """
        if not (self.embedding_configured() or self.is_configured()):
            # 不返回全零占位：零向量在 pgvector 里余弦距离是 NaN，会顺着检索一路变成
            # 界面上的乱码分数，且写入方（outbox）还会标成 completed，无人察觉。
            # 抛错让调用方走各自的降级路径。EmbeddingService 也会先行拦截。
            raise RuntimeError(
                "embedding 未配置（EMBEDDING_BASE_URL + EMBEDDING_API_KEY 或 chat provider）"
            )

        payload = {"model": self.embedding_model, "input": text}
        result = self._request_with_retry(
            lambda: self._http_post(
                f"{self._embedding_base_url()}/embeddings",
                payload,
                self._headers(self._embedding_api_key()),
                self.timeout,
            ),
            op="embed",
            model=self.embedding_model,
        )
        body = result.response.json()
        prompt_tokens, _ = self._usage_tokens(body)
        # ⚠️ **先解析向量再记账**：维度是判断「换模型后有没有把新旧向量混在一起」的
        # 唯一依据，必须记下来。顺带，响应畸形时不再记一条「成功」——
        # 那次调用确实没成功。
        values = body["data"][0]["embedding"]
        vector = [float(value) for value in values]
        self._observe(
            op="embed",
            model=self.embedding_model,
            outcome="ok",
            duration_ms=result.duration_ms,
            attempts=result.attempts,
            prompt_tokens=prompt_tokens,
            embedding_dimension=len(vector),
        )
        return vector
