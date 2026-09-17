"""L3 工具契约：只读、有界面、可审计。

**与上一版的区别（上一版已回退，见 `docs/分析_工具分层现状与越层调用.md`）：**

| | 上一版 | 这一版 |
|---|---|---|
| 封装对象 | ❌ Repository（分层错误） | ✅ Application Service / Query Service |
| 输入校验 | ❌ 手工 `str(params.get(...))` | ✅ Pydantic 模型，schema 由它生成 |
| 输出形状 | ❌ 无声明 | ✅ 每个工具声明 `output_schema` |
| 「查不到」 | ❌ 当成错误返回 | ✅ **三态**：success / empty / error |
| 审计 | ❌ 只有失败时一条 warning | ✅ 每次调用记 tool/参数摘要/耗时/条数/状态 |
| 消费方限制 | ❌ 无 | ✅ `allowed_consumers`（模型侧与内部侧可区分） |
| 只读约束 | ⚠️ 靠一条测试断言 | ✅ **注册时硬校验**，`read_only=False` 直接注册失败 |

**「只读」是这个系统的硬约束，不是风格偏好。** 需求治理里一切正式写入必须经人工评审
（`docs/流程_需求从提交到入库.md` §8）。所以这里不是「建议只读」，是**结构上不允许**：
`BaseTool.read_only` 恒为 True，`register()` 会拒绝任何把它设成 False 的工具。
"""

from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

logger = logging.getLogger(__name__)

__all__ = ["BaseTool", "ToolInput", "ToolResult", "ToolStatus"]

# 参数摘要里单个字符串值的截断长度。工具参数可能带上千字的需求正文，
# 原样打进日志既没用又危险（审计日志不该成为第二条数据副本）。
_ARG_TEXT_CHARS = 120


class ToolInput(BaseModel):
    """所有工具入参模型的基类。

    **`extra="forbid"` 是刻意的。** pydantic 默认忽略多余字段 —— 于是模型编一个
    不存在的参数名（`{"requirement_key": ..., "include_deleted": true}`）时，工具会
    **照默认值跑完**，返回一个看起来正常、其实没按模型意思办的结果。那比报错糟得多。

    拒绝多余字段能让模型**立刻知道**「这个参数不存在」，从而自己纠正。
    这与本项目其它请求体（如 `RequirementRevertRequest`）的约定一致。
    """

    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="before")
    @classmethod
    def _strip_text(cls, value: Any) -> Any:
        """所有字符串参数**先 strip 再校验**。

        没有这一步时，`requirement_key="   "` 能通过 `min_length=1`（长度是 3），
        然后被当成一个合法的编号去查，最后返回「没有编号为 `     ` 的需求」——
        把「参数没意义」伪装成了「需求不存在」。strip 之后它变成空串，
        直接被 `min_length` 拦下，报的是「参数不合法」。（实测踩到过。）
        """
        return value.strip() if isinstance(value, str) else value


class ToolStatus(str, Enum):
    """工具结果的三态。

    ⚠️ **`EMPTY` 不是错误。** 「没有相似需求」「这条需求不存在」对模型是**合法答案** ——
    上一版把它们都归成 error，会让模型以为工具坏了而反复重试。
    """

    SUCCESS = "success"   # 查到了，有结果
    EMPTY = "empty"       # 查了，但没有（正常答案）
    ERROR = "error"       # 调用本身失败（参数非法 / 依赖报错 / 超时）


@dataclass(frozen=True, slots=True)
class ToolResult:
    """一次工具调用的结果。"""

    status: ToolStatus
    result: Any = None
    message: str | None = None
    duration_ms: float = 0.0

    @property
    def is_error(self) -> bool:
        return self.status is ToolStatus.ERROR

    @property
    def is_empty(self) -> bool:
        return self.status is ToolStatus.EMPTY

    @staticmethod
    def success(result: Any, *, duration_ms: float = 0.0) -> "ToolResult":
        return ToolResult(status=ToolStatus.SUCCESS, result=result, duration_ms=duration_ms)

    @staticmethod
    def empty(message: str, *, result: Any = None, duration_ms: float = 0.0) -> "ToolResult":
        """查了但没有。**不是错误** —— 消息要说清是「没有」而不是「失败」。"""
        return ToolResult(
            status=ToolStatus.EMPTY, result=result, message=message, duration_ms=duration_ms
        )

    @staticmethod
    def error(message: str, *, duration_ms: float = 0.0) -> "ToolResult":
        return ToolResult(
            status=ToolStatus.ERROR, result=None, message=message, duration_ms=duration_ms
        )


class BaseTool(ABC):
    """所有只读工具的基类。

    子类声明九样东西（对应设计要求的完整描述），实现 `execute`：

        class SearchRequirementsTool(BaseTool):
            name = "search_requirements"
            description = "按语义检索历史需求……"
            input_model = SearchRequirementsInput          # Pydantic，唯一输入事实源
            output_schema = {"type": "object", "properties": {...}}
            allowed_consumers = ("analysis", "assistant")
            max_result_count = 10
            audit_event_name = "tool.search_requirements"

            def execute(self, params: SearchRequirementsInput) -> ToolResult: ...
    """

    # —— 必需 ——
    name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    input_model: ClassVar[type[BaseModel]]
    output_schema: ClassVar[dict[str, Any]] = {}

    # —— 只读：本系统里恒为 True，`register()` 会校验（见模块 docstring）——
    read_only: ClassVar[bool] = True

    # —— 谁能调它。`"analysis"` = 固定分析流程；`"assistant"` = 将来的通用助手 ——
    allowed_consumers: ClassVar[tuple[str, ...]] = ("analysis",)

    # —— 资源约束。这些都是**硬上限**，调用方只能要得更少 ——
    timeout_seconds: ClassVar[float] = 5.0
    max_result_count: ClassVar[int] = 20

    # —— 审计事件名（进结构化日志）——
    audit_event_name: ClassVar[str] = ""

    @abstractmethod
    def execute(self, params: Any) -> ToolResult:
        """执行查询。`params` 是**已校验过的** `input_model` 实例。"""
        raise NotImplementedError

    # ── 对外的描述 ────────────────────────────────────────────────────────

    def descriptor(self) -> dict[str, Any]:
        """工具的完整描述（九项）—— 注册表与运维视图用这个。"""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_model.model_json_schema(),
            "output_schema": self.output_schema,
            "read_only": self.read_only,
            "allowed_consumers": list(self.allowed_consumers),
            "timeout": self.timeout_seconds,
            "max_result_count": self.max_result_count,
            "audit_event_name": self.audit_event_name or f"tool.{self.name}",
        }

    def llm_schema(self) -> dict[str, Any]:
        """给 LLM 看的最小形状（将来接 function calling 时用这份）。"""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.input_model.model_json_schema(),
        }

    # ── 执行入口 ──────────────────────────────────────────────────────────

    def run(self, raw: dict[str, Any] | None = None) -> ToolResult:
        """校验 → 计时 → 执行 → 审计。**调用方只该走这里，不要直接调 `execute`。**

        三种失败都被兜住并转成 `ToolResult`（不向上抛）：
        - 入参不合 schema → `ERROR`（附 pydantic 的报错，便于模型自己纠正）
        - `execute` 抛异常 → `ERROR`
        - 结果为空 → 由 `execute` 返回 `EMPTY`

        为什么要这层：工具失败是**模型能自己纠正的输入**（换参数、换工具），
        不是该中断整条链路的错误。上一版在这里有个真实缺陷 —— `except` 之后隐式
        `return None`，调用方读 `result.status` 会 AttributeError。
        """
        started = time.monotonic()

        try:
            params = self.input_model.model_validate(raw or {})
        except ValidationError as exc:
            duration = _ms_since(started)
            result = ToolResult.error(f"参数不合法：{_brief_validation(exc)}", duration_ms=duration)
            self._audit(raw, result)
            return result

        try:
            result = self._execute_with_timeout(params)
        except TimeoutError:
            duration = _ms_since(started)
            result = ToolResult.error(
                f"工具 {self.name} 超过 {self.timeout_seconds}s 未返回，已放弃等待",
                duration_ms=duration,
            )
            self._audit(raw, result)
            return result
        except Exception as exc:  # noqa: BLE001 —— 见 docstring：故意兜住一切
            duration = _ms_since(started)
            result = ToolResult.error(f"{type(exc).__name__}: {exc}", duration_ms=duration)
            self._audit(raw, result)
            return result

        final = ToolResult(
            status=result.status,
            result=result.result,
            message=result.message,
            duration_ms=_ms_since(started),
        )
        self._audit(raw, final)
        return final

    def _execute_with_timeout(self, params: Any) -> ToolResult:
        """带超时地执行 `execute`。

        **超时的语义是「不再等它」，不是「取消它」。** Python 杀不掉线程，
        所以超时后那个调用仍在后台跑 —— 这是它唯一的诚实描述。
        要真正取消，得靠下层（Postgres 的 `statement_timeout` / HTTP 客户端超时）。

        为什么用**每次新建的守护线程**而不是共享线程池：共享池一旦被几个卡住的调用
        占满，后续调用会**排在队里无限等** —— 那比没有超时还糟。每次新建的线程
        随进程退出而消失，不会累积。当前工具全是本地库查询（毫秒级），
        建线程的开销可以忽略。
        """
        box: dict[str, Any] = {}

        def _work() -> None:
            try:
                box["result"] = self.execute(params)
            except BaseException as exc:  # noqa: BLE001 —— 原样带回主线程再分类
                box["error"] = exc

        worker = threading.Thread(target=_work, name=f"tool-{self.name}", daemon=True)
        worker.start()
        worker.join(self.timeout_seconds)
        if worker.is_alive():
            raise TimeoutError(self.name)
        if "error" in box:
            raise box["error"]
        return box["result"]

    # ── 审计 ──────────────────────────────────────────────────────────────

    def _audit(self, raw: dict[str, Any] | None, result: ToolResult) -> None:
        """每次调用记一条结构化日志：工具名 / 参数摘要 / 耗时 / 结果条数 / 状态。

        **写日志而不是写库**：写 `audit_event` 表需要事务，而 L3 层一旦能写库，
        「只读」就不再是结构上的保证、而只是一条纪律。审计要的可见性，
        结构化日志同样给得到，且不会让一个只读层拿到事务。
        """
        count: object = "-"
        if isinstance(result.result, (list, dict)):
            count = len(result.result)
        elif isinstance(result.result, dict) and "count" in result.result:
            count = result.result["count"]

        if result.is_error:
            logger.warning(
                "event=tool_call tool=%s args=%s duration_ms=%.1f status=%s error=%s",
                self.name, _digest(raw), result.duration_ms, result.status.value, result.message,
            )
        else:
            logger.info(
                "event=tool_call tool=%s args=%s duration_ms=%.1f status=%s count=%s",
                self.name, _digest(raw), result.duration_ms, result.status.value, count,
            )


def _ms_since(started: float) -> float:
    return round((time.monotonic() - started) * 1000, 1)


def _brief_validation(exc: ValidationError) -> str:
    """把 pydantic 的报错压成一行，给模型看时只需知道「哪个字段不对」。"""
    parts = []
    for error in exc.errors()[:3]:
        location = ".".join(str(item) for item in error.get("loc") or ())
        parts.append(f"{location}: {error.get('msg')}")
    return "；".join(parts)


def _digest(raw: dict[str, Any] | None) -> str:
    """参数摘要 —— 长文本截断。审计日志不该成为第二条数据副本。"""
    if not raw:
        return "{}"
    parts = []
    for key, value in list(raw.items())[:8]:
        if isinstance(value, str) and len(value) > _ARG_TEXT_CHARS:
            value = f"{value[:_ARG_TEXT_CHARS]}…(共{len(value)}字)"
        parts.append(f"{key}={value!r}")
    return "{" + ", ".join(parts) + "}"
