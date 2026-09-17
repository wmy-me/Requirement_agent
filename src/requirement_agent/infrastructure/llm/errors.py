"""LLM 调用的错误分类（B3.1b）。

追加实施文档 §4.5 要求「区分可重试错误、限流错误、参数错误和内容错误」。
分类不是为了好看 —— 它**决定两件事**：要不要重试同一个模型，以及要不要换备用模型。

## 分类与处置

| 类别 | 典型来源 | 重试同一模型？ | 换备用模型？ |
|---|---|---|---|
| `rate_limited` | HTTP 429 | ✅ 退避后重试 | ✅ 重试耗尽后换 |
| `retryable` | 408/409/425/5xx | ✅ | ✅ |
| `transport` | 连接失败、超时 | ✅ | ✅ |
| `content` | 响应不是合法 JSON / 缺字段 | ❌ 重试也是一样的结果 | ✅ **换个模型可能就合规** |
| `invalid_request` | 400 / 422 / 401 / 403 / 404 | ❌ | ❌ **不做** |
| `unknown` | 其它 | ❌ | ✅ |

## 为什么 `invalid_request` 既不重试也不换模型

请求本身有问题（schema 写错、模型名不存在、密钥无效）—— **换谁都是一样的结果**。
换备用模型只会把「代码里的 bug」伪装成「主模型不太行」，然后浪费一轮调用。
这类错误应当**直接冒出来**让人看见。

这与 `_RETRYABLE_STATUS` 的口径一致（那个集合早就把 4xx 排除在重试之外），
这里只是把「不重试」进一步推成「不降级」。
"""

from __future__ import annotations

from typing import Final, Literal

import httpx

__all__ = [
    "FALLBACK_WORTHY",
    "ErrorKind",
    "classify_error",
    "error_summary",
    "is_fallback_worthy",
]

ErrorKind = Literal[
    "rate_limited", "retryable", "transport", "content", "invalid_request", "unknown"
]

#: 值得换备用模型的类别。见模块 docstring 的表。
FALLBACK_WORTHY: Final = frozenset({"rate_limited", "retryable", "transport", "content", "unknown"})

#: 限流：单独一类，因为它的处置不同（应该更晚重试、更容易触发降级告警）
_RATE_LIMITED: Final = 429


def classify_error(exc: BaseException) -> ErrorKind:
    """把异常归到一档。**永不抛** —— 分类本身出问题不该盖住原始异常。"""
    try:
        return _classify(exc)
    except Exception:  # noqa: BLE001 —— 见 docstring
        return "unknown"


def _classify(exc: BaseException) -> ErrorKind:
    # 模型返回了东西，但内容不可用（不是 JSON、缺 data、JSON 解码失败）。
    # ⚠️ 这几类**值得换备用模型**：换个模型可能就给出合规输出。
    if isinstance(exc, (ValueError, KeyError, TypeError)):
        return "content"

    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == _RATE_LIMITED:
            return "rate_limited"
        if status >= 500 or status in (408, 409, 425):
            return "retryable"
        if 400 <= status < 500:
            # 400/401/403/404/422：请求或凭据本身的问题，换模型救不了
            return "invalid_request"
        return "unknown"

    if isinstance(exc, httpx.TransportError):
        # 含超时、连接失败、DNS 失败等网络层异常
        return "transport"

    return "unknown"


def is_fallback_worthy(kind: ErrorKind) -> bool:
    """这个类别值不值得换备用模型。见模块 docstring。"""
    return kind in FALLBACK_WORTHY


def error_summary(exc: BaseException) -> str:
    """一行错误摘要，用于 ① 降级链沿途的记录 ② 最终抛出的异常消息。

    只取第一行且截断 —— 完整的 traceback 在日志里，这里要的是「能进一行」。
    """
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    return f"{classify_error(exc)}:{type(exc).__name__}: {text[:180]}"
