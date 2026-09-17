"""工具注册表：存类不存实例，且**在注册时就挡住不该存在的东西**。

上一版这里只做了三件事（存类、去重、列 schema），把「只读」留给测试去断言。
这一版改成**注册时硬校验** —— 因为「测试会拦住」意味着「有人绕过测试就能进去」，
而这是系统的安全边界，不该只靠测试。

`register()` 会拒绝：

1. **`read_only=False`** —— 需求治理里一切正式写入必须经人工评审，L3 不允许有写工具。
2. **裁决类名字** —— 即使有人把它写成只读的转发，这个**名字**本身就不该出现在工具集里
   （`approve_review` 之类会让读代码的人以为模型能审批）。
3. **重名** —— 静默覆盖会让其中一个工具**永远调不到**，而表面看不出异常。
4. **没有 name / description / input_model** —— 缺任何一项，消费方就拿不到完整描述。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from requirement_agent.tools.base import BaseTool

__all__ = [
    "FORBIDDEN_NAMES",
    "TOOLS",
    "create",
    "get_class",
    "descriptors",
    "for_consumer",
    "llm_schemas",
    "names",
    "register",
]

# 名字 → 工具类。**值是类不是实例**：每次 `create()` 新建，工具天然无状态。
TOOLS: dict[str, type[BaseTool]] = {}

# **永远不该出现在工具集里的名字。** 不是「暂时不给模型」，是「不该存在」——
# 有了它们，读代码的人会以为模型能做裁决。裁决只有一条路：
# 人工审核 → POST /reviews/submit → commit_requirement_node（见流程文档 §8）。
FORBIDDEN_NAMES: frozenset[str] = frozenset(
    {
        "approve_review", "reject_review", "submit_review", "submit_review_decision",
        "commit_requirement", "merge_requirement", "revert_requirement",
        "create_requirement", "create_requirement_version", "update_current_version",
        "sync_features", "confirm_relation", "confirm_capability", "review_title",
        "create_capability", "create_constraint", "delete_requirement",
        "modify_historical_version",
        # 万能工具（设计要求里点名禁止的）
        "execute_sql", "run_repository_method", "call_service", "generic_database_tool",
    }
)


_LOADED = False


def _ensure_loaded() -> None:
    """把工具模块 import 进来（`@register` 在 import 时执行）。

    **为什么是懒加载而不是在 `tools/__init__.py` 里直接 import。**

    导入一个包里的**子模块**会先执行那个包的 `__init__`。工作流（`workflows/agents_nodes`）
    要经 `tools.invoker` 调工具，于是会触发 `tools/__init__`；如果它在那里一口气 import
    全部工具，就会连带把 `preview_merge_impact → application.review_service →
    workflows.commit_nodes` 拉进来 —— 而 `workflows` 此时正处在**半初始化**状态，
    循环导入当场炸（实测踩到，13 个测试文件收集失败）。

    改成懒加载后，`tools/__init__` 只导出契约与注册表，import 它不再牵连业务模块；
    真正的注册发生在**第一次问「有哪些工具」**的时候。
    """
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    import importlib
    import pkgutil

    import requirement_agent.tools as package

    # **扫目录，不维护手写清单** —— 手写清单是个会过期的第二事实源：
    # 第一版我凭印象列了两条早已不存在的模块名（`find_streams_by_capability` /
    # `get_capabilities`，那是**上一版**工具包的），一调用就 ModuleNotFoundError。
    # 排除本包的支撑模块（契约 / 注册表 / 调用入口），其余都是工具。
    support = {"base", "registry", "invoker", "__init__"}
    for info in pkgutil.iter_modules(package.__path__):
        if info.name in support:
            continue
        importlib.import_module(f"requirement_agent.tools.{info.name}")


def get_class(name: str) -> type[BaseTool] | None:
    """按名字取工具**类**（不是实例）—— 调用方需要看 `allowed_consumers` 等类属性时用。"""
    _ensure_loaded()
    return TOOLS.get(name)


def register(cls: type[BaseTool]) -> type[BaseTool]:
    """把工具类登记进注册表。用作类装饰器。"""
    name = (getattr(cls, "name", "") or "").strip()

    if not name:
        raise ValueError(f"{cls.__name__} 没有设置 name，无法注册")
    if name in FORBIDDEN_NAMES:
        raise ValueError(
            f"工具名 `{name}` 是禁止的 —— 裁决类/万能类能力不得做成工具（见 registry 顶部说明）"
        )
    if getattr(cls, "read_only", True) is not True:
        raise ValueError(
            f"工具 `{name}` 声明了 read_only=False。L3 层**没有写工具** —— "
            "正式写入必须经人工评审（docs/流程_需求从提交到入库.md §8）"
        )
    if not (getattr(cls, "description", "") or "").strip():
        raise ValueError(f"工具 `{name}` 没有 description —— 消费方无法判断何时该用它")
    if not isinstance(getattr(cls, "input_model", None), type) or not issubclass(
        cls.input_model, BaseModel
    ):
        raise ValueError(f"工具 `{name}` 的 input_model 必须是 Pydantic 模型（输入校验的唯一事实源）")
    if name in TOOLS and TOOLS[name] is not cls:
        raise ValueError(f"工具名 `{name}` 被 {TOOLS[name].__name__} 占用了")

    TOOLS[name] = cls
    return cls


def names() -> list[str]:
    """已注册的工具名（排序）。"""
    _ensure_loaded()
    return sorted(TOOLS)


def create(name: str) -> BaseTool | None:
    """按名字新建实例；不存在返回 None。"""
    _ensure_loaded()
    cls = TOOLS.get(name)
    return cls() if cls is not None else None


def for_consumer(consumer: str) -> list[type[BaseTool]]:
    """某类消费方**允许**调用的工具类。

    `"analysis"` = 固定分析流程；`"assistant"` = 将来的通用需求库助手。
    把两者分开是为了让「某个工具只给谁用」成为**声明**而不是口头约定 ——
    §六 的设计要求里有这一项。
    """
    return [TOOLS[name] for name in names() if consumer in TOOLS[name].allowed_consumers]


def llm_schemas(consumer: str) -> list[dict[str, Any]]:
    """某类消费方可见的工具清单（给 LLM 的那份）。"""
    return [cls().llm_schema() for cls in for_consumer(consumer)]


def descriptors() -> list[dict[str, Any]]:
    """全部工具的完整描述（九项）—— 运维视图 / 文档生成用。"""
    return [TOOLS[name]().descriptor() for name in names()]
