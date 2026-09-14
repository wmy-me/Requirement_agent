"""LLMProvider 的请求构造与重试行为测试。

传输层（`_http_post` / `_http_stream`）在测试里被替换成假实现，既不碰真实网络
也不引入 HTTP mock 依赖；退避基数设为 0，避免测试真的等待。
"""

from collections.abc import Iterator

import httpx
import pytest

from requirement_agent.config.settings import Settings
from requirement_agent.infrastructure.llm import openai_provider
from requirement_agent.infrastructure.llm.openai_provider import LLMProvider, llm_call_stats

_SSE_OK = [
    'data: {"choices":[{"delta":{"content":"你好"}}]}',
    "",
    'data: {"choices":[{"delta":{"content":"世界"}}]}',
    "data: [DONE]",
]

# 部分网关会在流末回传 usage（choices 为空的收尾块）
_SSE_WITH_USAGE = [
    'data: {"choices":[{"delta":{"content":"你好"}}]}',
    'data: {"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":3}}',
    "data: [DONE]",
]


@pytest.fixture(autouse=True)
def _reset_llm_stats() -> Iterator[None]:
    """计量是模块级全局状态，逐用例重置，避免相互污染。"""
    with openai_provider._STATS_LOCK:
        for key in openai_provider._LLM_CALL_STATS:
            openai_provider._LLM_CALL_STATS[key] = 0.0
    yield


@pytest.fixture()
def provider() -> LLMProvider:
    p = LLMProvider()
    p.api_key = "test-key"
    p.base_url = "https://llm.test/v1"
    p.model = "test-model"
    p.embedding_model = "test-embedding"
    p.temperature = None
    p.max_retries = 2
    p.retry_backoff = 0.0
    return p


def _response(status: int, payload: dict | None = None) -> httpx.Response:
    request = httpx.Request("POST", "https://llm.test/v1/chat/completions")
    if payload is None:
        return httpx.Response(status, request=request)
    return httpx.Response(status, json=payload, request=request)


def _chat_ok(text: str) -> httpx.Response:
    return _response(200, {"choices": [{"message": {"content": text}}]})


class _StubStream:
    """替代 `httpx.stream` 返回的上下文管理器。"""

    def __init__(self, response: object) -> None:
        self._response = response

    def __enter__(self) -> object:
        return self._response

    def __exit__(self, *exc_info: object) -> bool:
        return False


class _StubStreamResponse:
    """可控的流式响应：可在第 `fail_at` 行抛出网络异常，模拟半途断流。"""

    def __init__(self, lines: list[str], fail_at: int | None = None) -> None:
        self._lines = lines
        self._fail_at = fail_at

    def raise_for_status(self) -> None:
        return None

    def iter_lines(self):
        for index, line in enumerate(self._lines):
            if self._fail_at is not None and index == self._fail_at:
                raise httpx.ReadError("stream broke")
            yield line


# ── 请求构造 ────────────────────────────────────────────────────────────


def test_payload_omits_temperature_when_unset(provider: LLMProvider) -> None:
    payload = provider._chat_payload("你好", "系统提示")
    assert "temperature" not in payload
    assert payload["model"] == "test-model"
    assert payload["messages"] == [
        {"role": "system", "content": "系统提示"},
        {"role": "user", "content": "你好"},
    ]


def test_payload_includes_temperature_when_configured(provider: LLMProvider) -> None:
    # 0.0 是合法取值，不能被 None 判断误伤
    provider.temperature = 0.0
    assert provider._chat_payload("你好", None)["temperature"] == 0.0


def test_stream_payload_sets_stream_flag(provider: LLMProvider) -> None:
    assert provider._chat_payload("你好", None, stream=True)["stream"] is True


def test_blank_temperature_env_is_treated_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    # .env 里写 `LLM_TEMPERATURE=` 是常见写法，不应导致启动时 ValidationError
    monkeypatch.setenv("LLM_TEMPERATURE", "")
    assert Settings().llm_temperature is None


# ── 重试 ────────────────────────────────────────────────────────────────


def test_generate_retries_on_retryable_status_then_succeeds(provider: LLMProvider) -> None:
    calls: list[dict] = []

    def fake_post(url, payload, headers, timeout):
        calls.append(payload)
        return _response(503) if len(calls) == 1 else _chat_ok("完成")

    provider._http_post = fake_post
    assert provider.generate("你好") == "完成"
    assert len(calls) == 2


def test_generate_does_not_retry_on_client_error(provider: LLMProvider) -> None:
    calls: list[int] = []

    def fake_post(url, payload, headers, timeout):
        calls.append(1)
        return _response(400)

    provider._http_post = fake_post
    with pytest.raises(httpx.HTTPStatusError):
        provider.generate("你好")
    assert len(calls) == 1


def test_generate_raises_after_exhausting_retries(provider: LLMProvider) -> None:
    calls: list[int] = []

    def fake_post(url, payload, headers, timeout):
        calls.append(1)
        return _response(429)

    provider._http_post = fake_post
    with pytest.raises(httpx.HTTPStatusError):
        provider.generate("你好")
    assert len(calls) == provider.max_retries + 1


def test_generate_retries_on_transport_error(provider: LLMProvider) -> None:
    calls: list[int] = []

    def fake_post(url, payload, headers, timeout):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectError("connection refused")
        return _chat_ok("恢复")

    provider._http_post = fake_post
    assert provider.generate("你好") == "恢复"
    assert len(calls) == 2


def test_no_retry_when_max_retries_is_zero(provider: LLMProvider) -> None:
    provider.max_retries = 0
    calls: list[int] = []

    def fake_post(url, payload, headers, timeout):
        calls.append(1)
        return _response(500)

    provider._http_post = fake_post
    with pytest.raises(httpx.HTTPStatusError):
        provider.generate("你好")
    assert len(calls) == 1


# ── 流式 ────────────────────────────────────────────────────────────────


def test_generate_stream_yields_incremental_text(provider: LLMProvider) -> None:
    provider._http_stream = lambda *args: _StubStream(_StubStreamResponse(_SSE_OK))
    assert list(provider.generate_stream("你好")) == ["你好", "世界"]


def test_generate_stream_retries_before_first_chunk(provider: LLMProvider) -> None:
    calls: list[int] = []

    def fake_stream(url, payload, headers, timeout):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectError("connection refused")
        return _StubStream(_StubStreamResponse(_SSE_OK))

    provider._http_stream = fake_stream
    assert "".join(provider.generate_stream("你好")) == "你好世界"
    assert len(calls) == 2


def test_generate_stream_does_not_retry_after_first_chunk(provider: LLMProvider) -> None:
    """已在产出内容后再失败必须抛错，不能重试——否则调用方会收到重复的半截文本。"""
    calls: list[int] = []
    broken = _StubStreamResponse(_SSE_OK, fail_at=2)  # 第 3 行抛错，此时已产出「你好」

    def fake_stream(url, payload, headers, timeout):
        calls.append(1)
        return _StubStream(broken)

    provider._http_stream = fake_stream
    received: list[str] = []
    with pytest.raises(httpx.ReadError):
        for piece in provider.generate_stream("你好"):
            received.append(piece)
    assert received == ["你好"]
    assert len(calls) == 1


# ── embedding ───────────────────────────────────────────────────────────


def test_embed_retries_and_returns_vector(provider: LLMProvider) -> None:
    calls: list[dict] = []

    def fake_post(url, payload, headers, timeout):
        calls.append(payload)
        if len(calls) == 1:
            return _response(429)
        return _response(200, {"data": [{"embedding": [0.1, 0.2]}]})

    provider._http_post = fake_post
    assert provider.embed("文本") == [0.1, 0.2]
    assert len(calls) == 2


# ── 可观测性计量 ────────────────────────────────────────────────────────


def test_successful_call_counts_and_reports_tokens(provider: LLMProvider) -> None:
    provider._http_post = lambda *args: _response(
        200,
        {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 7},
        },
    )
    provider.generate("你好")

    stats = llm_call_stats()
    assert stats["calls_total"] == 1
    assert stats["failures_total"] == 0
    assert stats["retries_total"] == 0
    assert stats["prompt_tokens_total"] == 12
    assert stats["completion_tokens_total"] == 7


def test_call_without_usage_still_counted(provider: LLMProvider) -> None:
    # 响应体没有 usage 段时不应报错，token 按 0 计
    provider._http_post = lambda *args: _chat_ok("ok")
    provider.generate("你好")
    stats = llm_call_stats()
    assert stats["calls_total"] == 1
    assert stats["prompt_tokens_total"] == 0


def test_failed_call_counts_as_failure(provider: LLMProvider) -> None:
    provider._http_post = lambda *args: _response(400)
    with pytest.raises(httpx.HTTPStatusError):
        provider.generate("你好")
    stats = llm_call_stats()
    assert stats["calls_total"] == 1
    assert stats["failures_total"] == 1


def test_retry_is_counted(provider: LLMProvider) -> None:
    calls: list[int] = []

    def fake_post(url, payload, headers, timeout):
        calls.append(1)
        return _response(503) if len(calls) == 1 else _chat_ok("ok")

    provider._http_post = fake_post
    provider.generate("你好")
    stats = llm_call_stats()
    assert stats["calls_total"] == 1  # 一次逻辑调用
    assert stats["retries_total"] == 1  # 其中含一次重试


def test_stream_collects_usage_when_gateway_sends_it(provider: LLMProvider) -> None:
    provider._http_stream = lambda *args: _StubStream(_StubStreamResponse(_SSE_WITH_USAGE))
    assert "".join(provider.generate_stream("你好")) == "你好"

    stats = llm_call_stats()
    assert stats["calls_total"] == 1
    assert stats["prompt_tokens_total"] == 5
    assert stats["completion_tokens_total"] == 3


def test_stats_snapshot_is_a_copy() -> None:
    snapshot = llm_call_stats()
    snapshot["calls_total"] = 999
    assert llm_call_stats()["calls_total"] == 0
