"""Cooperative ownership and deadline checks for synchronous analysis graphs.

This module never promises to interrupt a call already inside a blocking model,
HTTP client, or database driver.  It fences the boundaries before and after
those calls, which prevents a stale executor from starting the next one.
"""

from __future__ import annotations

import contextlib
import contextvars
import time
from collections.abc import Callable, Iterator


class ExecutionStopped(RuntimeError):
    """The run was paused/cancelled/reclaimed while this executor was active."""


class AnalysisBudgetExhausted(RuntimeError):
    """The persisted wall-clock analysis budget has elapsed."""


_guard: contextvars.ContextVar[Callable[[], bool] | None] = contextvars.ContextVar("agent_execution_guard", default=None)
_deadline: contextvars.ContextVar[float | None] = contextvars.ContextVar("agent_execution_deadline", default=None)


@contextlib.contextmanager
def bind_execution(guard: Callable[[], bool] | None, deadline_monotonic: float | None) -> Iterator[None]:
    guard_token = _guard.set(guard)
    deadline_token = _deadline.set(deadline_monotonic)
    try:
        yield
    finally:
        _guard.reset(guard_token)
        _deadline.reset(deadline_token)


def remaining_seconds() -> float | None:
    deadline = _deadline.get()
    return None if deadline is None else max(0.0, deadline - time.monotonic())


def check_execution() -> None:
    guard = _guard.get()
    if guard is not None and not guard():
        raise ExecutionStopped("agent run no longer owns execution")
    remaining = remaining_seconds()
    if remaining is not None and remaining <= 0:
        raise AnalysisBudgetExhausted("agent analysis budget exhausted")
