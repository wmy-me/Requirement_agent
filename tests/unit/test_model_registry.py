"""任务级模型路由（B3.1）。

B3.1 之前，「用哪个模型」由**一个全局开关**决定：`LLM_PROVIDER` 经三个 if-else
属性作用到所有调用。抽取、分析、风险、叙述、embedding 全用同一个模型 ——
想给某一个任务换模型只能改全局 env，而某家网关抖动时没有任何备用。

这个文件钉住四件事：
1. **默认（`MODEL_ROUTES` 为空）行为与 B3.1 之前逐字节一致** —— 这是本批能独立
   提交、独立回滚的前提；
2. 配了路由就按路由走，且**备用链去重**；
3. 配错的名字**不抛**、退到全局，但要**能被诊断出来**；
4. `embedding` / `vision` 的全局兜底**不走 chat 模型名**。
"""

from __future__ import annotations

import json

import pytest

from requirement_agent.config.settings import settings
from requirement_agent.infrastructure.llm.model_registry import (
    SUPPORTED_PROVIDERS,
    TASK_TYPES,
    ModelRegistry,
    ModelSpec,
    TaskRoute,
)


@pytest.fixture(autouse=True)
def _isolate_routes():
    """这个文件会改 `settings.model_routes`，跑完还原，免得污染其它测试。"""
    original = settings.model_routes
    yield
    settings.model_routes = original


def _with_routes(raw: str) -> ModelRegistry:
    settings.model_routes = json.loads(raw)
    return ModelRegistry(settings)


# ── ① 默认行为：与 B3.1 之前一致 ──────────────────────────────────────────


def test_empty_config_falls_back_to_the_global_llm_settings() -> None:
    """**最重要的一条。** 没配 `MODEL_ROUTES` 时全部走全局配置。

    这条要是坏了，「引入路由」就会改变每一次实际调用用的模型 ——
    而用户什么都没配，不该有任何行为变化。
    """
    settings.model_routes = {}
    registry = ModelRegistry(settings)

    for task in ("extract", "analyze", "risk", "narrative"):
        spec = registry.get_chat_model(task)
        assert spec.provider == settings.llm_provider, task
        assert spec.model == settings.active_llm_model, task
    assert registry.unrecognized() == {}


def test_embedding_global_fallback_uses_the_embedding_model() -> None:
    """⚠️ embedding 的全局兜底**不能**是 chat 模型名 —— 拿去请求会 404。

    这是实施时真踩到的：第一版 `get_embedding_model` 复用了 `resolve()`，
    而它的全局兜底取的是 `active_llm_model`（deepseek-v4-flash）。
    """
    settings.model_routes = {}
    spec = ModelRegistry(settings).get_embedding_model()

    assert spec.model == settings.embedding_model
    assert spec.model != settings.active_llm_model or settings.embedding_model == settings.active_llm_model


def test_vision_is_an_unsupported_placeholder_by_default() -> None:
    """vision 没有全局兜底 —— 配置里根本没有视觉模型字段。

    返回一个 `supported == False` 的占位，而不是假装「chat 模型就是视觉模型」，
    免得将来接图片理解的人以为它已经配好了。
    """
    settings.model_routes = {}
    spec = ModelRegistry(settings).get_vision_model()

    assert spec.supported is False


# ── ② 路由生效 ────────────────────────────────────────────────────────────


def test_configured_route_wins_over_the_global_setting() -> None:
    registry = _with_routes(
        '{"risk": {"provider": "openai", "model": "gpt-4o-mini"}}'
    )

    risk = registry.get_chat_model("risk")
    assert (risk.provider, risk.model) == ("openai", "gpt-4o-mini")
    # 没配的任务照旧走全局
    assert registry.get_chat_model("extract").model == settings.active_llm_model


def test_fallback_chain_is_deduped() -> None:
    """同一条 (provider, model) 在链上出现两次没有意义，而且会让「降了几级」这个数虚高。"""
    registry = _with_routes(
        '{"analyze": {"provider": "deepseek", "model": "a", "fallbacks": ['
        '{"provider": "openai", "model": "b"},'
        '{"provider": "deepseek", "model": "a"},'
        '{"provider": "openai", "model": "b"}]}}'
    )

    assert [s.model for s in registry.get_fallback_chain("analyze")] == ["b"]


def test_fallback_chain_excludes_the_primary() -> None:
    registry = _with_routes(
        '{"analyze": {"provider": "deepseek", "model": "a",'
        ' "fallbacks": [{"provider": "openai", "model": "b"}]}}'
    )

    assert registry.get_chat_model("analyze").model == "a"
    assert [s.model for s in registry.get_fallback_chain("analyze")] == ["b"]


# ── ③ 配错了要可见，但别让服务起不来 ──────────────────────────────────────


def test_unknown_provider_falls_back_and_is_reported() -> None:
    """拼错的 provider 名 → 退到全局，并且**能被诊断出来**。

    不抛的理由：`MODEL_ROUTES` 是手写 JSON，写错一个键名是常事；
    而「这个任务的模型没配上，走全局」是可接受的降级。
    但**必须可见** —— 否则「我明明配了，怎么没生效」只能靠读代码猜。
    """
    registry = _with_routes('{"extract": {"provider": "typo", "model": "x"}}')

    assert registry.get_chat_model("extract").model == settings.active_llm_model
    problems = registry.unrecognized()
    assert "extract" in problems
    assert "provider" in problems["extract"]


def test_unknown_task_type_is_reported() -> None:
    """task_type 拼错（比如 `tpyo`）不会生效，但要说得出是哪个。"""
    registry = _with_routes('{"tpyo": {"provider": "deepseek", "model": "x"}}')

    problems = registry.unrecognized()
    assert "tpyo" in problems
    assert "task_type" in problems["tpyo"]


def test_missing_model_field_is_reported() -> None:
    registry = _with_routes('{"risk": {"provider": "deepseek"}}')

    assert "risk" in registry.unrecognized()
    assert registry.get_chat_model("risk").model == settings.active_llm_model


# ── ④ spec → LLMProvider ──────────────────────────────────────────────────


def test_provider_with_spec_uses_that_provider_credentials() -> None:
    """`spec` 决定走哪家网关 —— 密钥与 base_url 都跟着 provider 走，不是跟着全局。"""
    from requirement_agent.infrastructure.llm.openai_provider import LLMProvider

    provider = LLMProvider(spec=ModelSpec(provider="openai", model="gpt-4o-mini"))

    assert provider.provider_name == "openai"
    assert provider.model == "gpt-4o-mini"
    assert provider.base_url == settings.openai_base_url.rstrip("/")


def test_provider_with_unknown_spec_is_unconfigured_not_broken() -> None:
    """拼错的 provider → `is_configured()` 为 False → 走既有的「未配置 → 启发式」分支。

    表现与「没配模型」一致，而不是崩溃。"""
    from requirement_agent.infrastructure.llm.openai_provider import LLMProvider

    provider = LLMProvider(spec=ModelSpec(provider="typo", model="x"))

    assert provider.is_configured() is False


def test_provider_without_spec_is_unchanged() -> None:
    """**兼容红线**：无参构造逐字段等于全局配置 —— 6 处调用点与 19 条既有测试靠它。"""
    from requirement_agent.infrastructure.llm.openai_provider import LLMProvider

    provider = LLMProvider()

    assert provider.spec is None
    assert provider.task_type == ""
    assert provider.provider_name == settings.llm_provider
    assert provider.api_key == settings.active_llm_api_key
    assert provider.base_url == settings.active_llm_base_url.rstrip("/")
    assert provider.model == settings.active_llm_model


# ── ⑤ 常量自洽 ────────────────────────────────────────────────────────────


def test_task_types_cover_the_document_table() -> None:
    """追加实施文档 §4.2 列了五个任务；narrative 是第六个（本项目实际有的）。

    这条不是形式主义：`TASK_TYPES` 同时是 `unrecognized()` 的判据，
    漏一个就会把合法任务报成拼错。
    """
    assert {"extract", "analyze", "risk", "embedding", "vision"} <= set(TASK_TYPES)
    assert "narrative" in TASK_TYPES


def test_route_chain_puts_primary_first() -> None:
    route = TaskRoute(
        primary=ModelSpec("deepseek", "a"), fallbacks=(ModelSpec("openai", "b"),)
    )
    assert [s.model for s in route.chain()] == ["a", "b"]


def test_supported_providers_is_a_closed_enum() -> None:
    """支持的 provider 是**有限枚举**，不是任意字符串 —— 拼错的名字不该被当成新 provider。"""
    assert set(SUPPORTED_PROVIDERS) == {"deepseek", "openai"}
    assert ModelSpec(provider="typo", model="x").supported is False
    assert ModelSpec(provider="deepseek", model="").supported is False
