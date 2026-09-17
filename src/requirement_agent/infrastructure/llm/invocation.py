"""模型调用的记录挂钩（B3.1）。

**为什么是一个挂钩而不是直接写库。** `LLMProvider` 在 `infrastructure/llm/`，
`ModelInvocationRepository` 在 `infrastructure/db/repositories/` —— 让前者 import
后者会把「HTTP 客户端」和「数据库仓储」焊在一起：provider 的单元测试从此要拖起
一个库连接，而它现在 19 条测试一条都不碰数据库。

所以中间放一层：provider 只管**产出记录**，谁消费由组合根（`api/dependencies.py`）
装配。测试里换成一个收集列表就能断言「调了几次、记了什么」。

## `run_id` 怎么关联

调用点（图节点、技能、SSE 管线）并不会把 run_id 一路传进 provider —— 那要改
每一个函数签名。改用 `ContextVar`：调用方在进入一段工作时 `bind_run_id(run_id)`，
provider 在记录时读取。

`ContextVar` 而不是模块级全局，是为了**并发安全**：API 进程内多个请求线程各有各的
上下文；`run_in_threadpool` 也会把上下文复制进去（anyio 的行为），
所以 SSE 管线在线程池里发的调用照样能关联上。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "bind_prompt_version",
    "last_route_outcome",
    "set_last_route_outcome",
    "bind_run_id",
    "current_prompt_version",
    "current_run_id",
    "record_invocation",
    "set_recorder",
]

#: 当前正在跑的 run。没绑定时为 None —— 记录照样写，只是 run_id 为空。
_current_run_id: ContextVar[str | None] = ContextVar("llm_invocation_run_id", default=None)

#: 当前的 prompt 版本。由技能层在发请求前绑定 —— provider 不该知道 prompts 模块
#: （那是 skills 层的东西，让基础设施反向依赖它会把分层搞反）。
_current_prompt_version: ContextVar[str | None] = ContextVar(
    "llm_invocation_prompt_version", default=None
)

#: 最近一次路由调用的结果摘要（`{"degraded","source","reason","model"}` 或 None）。
#
# 用 ContextVar 而不是技能实例上的属性：技能（`AnalyzeSkill` 等）在
# `api/dependencies.py` 里是**进程级单例**，API 并发请求会共用同一个实例 ——
# 挂在实例上会出现「A 请求读到 B 请求的降级状态」。
#
# 由 `BaseSkill._generate_json` 在**每次调用开始时清空**，成功后再写入；
# 于是「读不到」= 这次调用没成功，调用方据此走「启发式降级」分支。
_last_route_outcome: ContextVar[dict[str, Any] | None] = ContextVar(
    "llm_last_route_outcome", default=None
)

#: 记录消费者。由组合根装配；**没装配时静默丢弃**（见 `record_invocation`）。
_recorder: Callable[[dict[str, Any]], None] | None = None
#: 记录器是否**已确定**。与「`_recorder is None`」是两回事 ——
#: 后者既可能表示「还没解析」也可能表示「被显式关掉了」。
_recorder_resolved = False


def current_run_id() -> str | None:
    return _current_run_id.get()


def current_prompt_version() -> str | None:
    return _current_prompt_version.get()


def set_last_route_outcome(payload: dict[str, Any] | None) -> None:
    """记下最近一次路由调用的结果。`None` 表示「还没成功过」。"""
    _last_route_outcome.set(payload)


def last_route_outcome() -> dict[str, Any] | None:
    return _last_route_outcome.get()


@contextmanager
def bind_run_id(run_id: str | None) -> Iterator[None]:
    """在这一段工作里把模型调用关联到 `run_id`。

    ⚠️ **退出时恢复原值**（而不是置 None）：嵌套调用时内层结束不该把外层的绑定抹掉。
    """
    token = _current_run_id.set(run_id)
    try:
        yield
    finally:
        _current_run_id.reset(token)


@contextmanager
def bind_prompt_version(version: str | None) -> Iterator[None]:
    """在这一段工作里标记 prompt 版本（由技能层在发请求前绑定，见模块 docstring）。"""
    token = _current_prompt_version.set(version)
    try:
        yield
    finally:
        _current_prompt_version.reset(token)


def set_recorder(recorder: Callable[[dict[str, Any]], None] | None) -> None:
    """显式指定记录消费者。传 `None` 表示**关掉记录**（单元测试里用）。

    不调它的话，第一次记录时按默认（写库）惰性装配 —— 见 `_resolve_recorder`。
    """
    global _recorder, _recorder_resolved
    _recorder = recorder
    _recorder_resolved = True


def _resolve_recorder() -> Callable[[dict[str, Any]], None] | None:
    """惰性装配默认记录器：写 `model_invocation` 表。

    ## 为什么是惰性、而不是在组合根显式装配

    第一版把装配放在 `api/dependencies.py` —— 那是 API 的组合根。**结果两个问题**：

    1. **Worker 路径不记录。** 渠道接入的分析走的是
       `workers/tasks.py` 自己的装配，它**不 import** `api.dependencies`。
       也就是说「渠道进来的分析永远不记模型调用」—— 而那是生产的主路径。
    2. **单元测试反而写库了。** API 的测试 import `api.app` 时会**全局**打开记录器，
       随后同进程里的 `test_llm_provider`（它 mock 掉 httpx、本该零副作用）
       开始往库里写行。实测一轮全量测试留下 36 行。

    惰性解析两个问题一起解决：**任何入口点第一次记录时自己装配好**，
    而单元测试显式 `set_recorder(None)` 关掉。

    ⚠️ import 放在函数里是刻意的：模块加载时 import 会让
    `infrastructure/llm` 反向依赖 `db/repositories`，而那正是要避免的耦合。
    """
    global _recorder, _recorder_resolved
    if _recorder_resolved:
        return _recorder
    _recorder_resolved = True
    try:
        from requirement_agent.infrastructure.db.repositories.model_invocation import (
            ModelInvocationRepository,
        )

        _recorder = ModelInvocationRepository().record
    except Exception as exc:  # noqa: BLE001 —— 装配失败不该让服务起不来
        logger.warning("event=model_invocation_recorder_unavailable error=%s: %s", type(exc).__name__, exc)
        _recorder = None
    return _recorder


def record_invocation(record: dict[str, Any]) -> None:
    """把一条调用记录交给消费者。**任何异常都吞掉，只告警。**

    与 `RunTracking` 同一条纪律：调用记录是**观测设施**，它坏了不该让
    「回答用户的提问」这件事失败。
    """
    recorder = _resolve_recorder()
    if recorder is None:
        return
    try:
        recorder(record)
    except Exception as exc:  # noqa: BLE001 —— 见 docstring
        logger.warning(
            "event=model_invocation_record_failed task_type=%s model=%s error=%s: %s",
            record.get("task_type"),
            record.get("model"),
            type(exc).__name__,
            exc,
        )
