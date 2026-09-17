"""L3 工具契约的测试（不碰数据库）。

这里钉的是**结构约束**，不是业务行为：

1. 注册表与本文件登记的清单**完全一致**（加工具忘了改名录会红）；
2. **只读是注册时的硬校验** —— `read_only=False` / 裁决类名字 / 缺 input_model
   都**注册不进去**（上一版只靠测试断言，等于「绕过测试就能进去」）；
3. `QueryService` 里**没有任何写方法**；
4. `run()` 的三种失败都被兜成 `ToolResult`，不向上抛。

业务行为（真实查询、三态语义、截断）在 `tests/integration/test_tool_execute.py`。
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from pydantic import BaseModel

from requirement_agent.application.requirement_query import RequirementQueryService
from requirement_agent.tools import TOOLS, BaseTool, ToolResult, ToolStatus
from requirement_agent.tools import create, descriptors, for_consumer, llm_schemas, names
from requirement_agent.tools.registry import FORBIDDEN_NAMES, register

# 本批次登记在册的工具。**刻意与本文件重复**：让「名录与实现对不上」变成一条失败的
# 测试，而不是一个安静的缺失。
EXPECTED_TOOLS = {
    # 批 1
    "search_requirements",
    "get_requirement_detail",
    "get_requirement_features",
    "get_requirement_versions",
    "list_requirements",
    # 批 2
    "compare_requirement_versions",
    "get_requirement_risks",
    "list_requirement_relations",
    "list_requirement_sources",
    "search_by_capability",
    "search_by_constraint",
    "search_features",
    "trace_requirement_sources",
    # B2 第二批（只加确定性的两个）
    "match_capabilities",
    "preview_merge_impact",
}

WRITE_PREFIXES = ("save", "create", "update", "delete", "commit", "insert", "upsert", "sync")


# ── 注册表 ────────────────────────────────────────────────────────────────


def test_registry_matches_expected_roster() -> None:
    assert set(names()) == EXPECTED_TOOLS, (
        f"缺：{EXPECTED_TOOLS - set(names())}；多：{set(names()) - EXPECTED_TOOLS}"
    )


def test_no_forbidden_names_registered() -> None:
    """裁决类 / 万能类名字一个都不能出现。"""
    assert not (set(names()) & FORBIDDEN_NAMES)


def test_create_returns_a_fresh_instance() -> None:
    """注册表**存类不存实例** —— 每次新建，工具天然无状态。"""
    first, second = create("search_requirements"), create("search_requirements")
    assert first is not None and second is not None and first is not second


def test_create_unknown_returns_none() -> None:
    assert create("no_such_tool") is None


# ── 注册时的硬校验（这一版新加的） ────────────────────────────────────────


def _make(cls_name: str, **attrs: Any) -> type[BaseTool]:
    base: dict[str, Any] = {
        "name": "tmp_probe",
        "description": "探针",
        "input_model": _ProbeInput,
    }
    base.update(attrs)
    return type(cls_name, (BaseTool,), base)


class _ProbeInput(BaseModel):
    pass


def test_read_only_false_is_rejected_at_registration() -> None:
    """**这是本系统的安全边界，不能只靠测试断言。** 写工具连注册都进不去。"""
    with pytest.raises(ValueError, match="read_only=False"):
        register(_make("WriteProbe", read_only=False))


@pytest.mark.parametrize("forbidden", ["approve_review", "commit_requirement", "execute_sql"])
def test_forbidden_names_are_rejected_at_registration(forbidden: str) -> None:
    with pytest.raises(ValueError, match="禁止"):
        register(_make("ProbeA", name=forbidden))


def test_missing_description_is_rejected() -> None:
    with pytest.raises(ValueError, match="description"):
        register(_make("ProbeB", description="  "))


def test_missing_input_model_is_rejected() -> None:
    """没有 Pydantic 输入模型就没有校验、也没有 schema —— 不给注册。"""
    with pytest.raises(ValueError, match="input_model"):
        register(_make("ProbeC", input_model=dict))  # type: ignore[arg-type]


def test_duplicate_name_is_rejected() -> None:
    with pytest.raises(ValueError, match="占用了"):
        register(_make("ProbeD", name="search_requirements"))


# ── 描述完整性（设计要求里的九项） ────────────────────────────────────────


@pytest.mark.parametrize("name", sorted(EXPECTED_TOOLS))
def test_every_tool_declares_the_nine_fields(name: str) -> None:
    tool = create(name)
    assert tool is not None
    d = tool.descriptor()

    assert d["name"] == name
    assert d["description"].strip()
    assert d["input_schema"].get("type") == "object"
    assert isinstance(d["output_schema"], dict) and d["output_schema"], "必须声明输出形状"
    assert d["read_only"] is True
    assert d["allowed_consumers"], "必须声明谁能调它"
    assert d["timeout"] > 0
    assert d["max_result_count"] >= 1
    assert d["audit_event_name"].startswith("tool.")


@pytest.mark.parametrize("name", sorted(EXPECTED_TOOLS))
def test_input_schema_is_generated_from_pydantic(name: str) -> None:
    """输入 schema 与校验用**同一个** Pydantic 模型 —— 不会漂移。"""
    tool = create(name)
    assert tool is not None
    schema = tool.input_model.model_json_schema()
    assert schema == tool.descriptor()["input_schema"]
    assert tool.llm_schema()["parameters"] == schema


def test_consumers_filter_works() -> None:
    analysis = {cls.name for cls in for_consumer("analysis")}
    assistant = {cls.name for cls in for_consumer("assistant")}
    assert analysis <= EXPECTED_TOOLS
    assert len(assistant) <= len(analysis)
    assert len(llm_schemas("analysis")) == len(analysis)


def test_descriptors_cover_every_tool() -> None:
    assert {item["name"] for item in descriptors()} == EXPECTED_TOOLS


# ── 不变式：这一层是只读的 ────────────────────────────────────────────────


def test_query_service_has_no_write_methods() -> None:
    """**QueryService 一个写方法都不能有。** 它的职责只有查询。"""
    offenders = [
        name
        for name, member in inspect.getmembers(RequirementQueryService, inspect.isfunction)
        if not name.startswith("_") and name.lower().startswith(WRITE_PREFIXES)
    ]
    assert not offenders, f"QueryService 出现了写方法：{offenders}"


def test_query_service_source_never_commits() -> None:
    """源码里不能出现 `commit()` / `rollback()` —— 只读层不该有事务语义。"""
    source = inspect.getsource(inspect.getmodule(RequirementQueryService))
    for forbidden in (".commit(", ".rollback(", "session.add("):
        assert forbidden not in source, f"QueryService 源码里出现了 `{forbidden}`"


def test_tools_do_not_import_repositories() -> None:
    """**这一条是上一版最大的错。** 工具封装 Service，不封装 Repository。

    上一版就是直接 `from ...db.repositories import ...`，被判定为分层错误后删掉重来。

    用 AST 检查**真实的 import 语句**，不是 grep 源码文本 —— 模块 docstring 里
    正好写着 `db.repositories` 这个反面例子，grep 会把它当成违规（第一版就这么误报了）。
    """
    import ast
    import pkgutil

    import requirement_agent.tools as tools_pkg

    offenders: list[str] = []
    for module_info in pkgutil.iter_modules(tools_pkg.__path__):
        module = __import__(f"{tools_pkg.__name__}.{module_info.name}", fromlist=["_"])
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and "repositories" in (node.module or ""):
                offenders.append(f"{module_info.name}:{node.lineno}")
            if isinstance(node, ast.Import):
                offenders.extend(
                    f"{module_info.name}:{node.lineno}"
                    for alias in node.names
                    if "repositories" in alias.name
                )
    assert not offenders, f"L3 工具不得直接依赖 Repository，违规处：{offenders}"


# ── run() 的兜底行为 ──────────────────────────────────────────────────────


def test_bad_params_return_error_not_raise() -> None:
    """入参不合 schema → `ERROR`（附可读原因），**不抛异常**。"""
    result = create("search_requirements").run({})  # 缺必填的 query
    assert result.status is ToolStatus.ERROR
    assert "query" in (result.message or "")


def test_unknown_params_are_rejected() -> None:
    result = create("get_requirement_detail").run(
        {"requirement_key": "REQ-000015", "写进来的多余字段": 1}
    )
    assert result.status is ToolStatus.ERROR


def test_execute_exception_becomes_error_not_none() -> None:
    """**上一版在这里有个真实缺陷**：`except` 之后隐式 `return None`，
    调用方读 `result.status` 会 AttributeError。这里断言返回的是 `ToolResult`。"""

    class Exploding(BaseTool):
        name = "exploding_probe"
        description = "总是失败"
        input_model = _ProbeInput
        output_schema = {"type": "object"}

        def execute(self, params: _ProbeInput) -> ToolResult:
            raise RuntimeError("boom")

    result = Exploding().run({})
    assert isinstance(result, ToolResult)
    assert result.status is ToolStatus.ERROR
    assert "boom" in (result.message or "")


def test_run_records_duration() -> None:
    result = create("search_requirements").run({})
    assert result.duration_ms >= 0


def test_tool_status_has_three_states() -> None:
    """三态是设计要求：`empty` 与 `error` **必须分开** ——
    「没有相似需求」是正常答案，上一版把它当错误，会让模型以为工具坏了。"""
    assert {status.value for status in ToolStatus} == {"success", "empty", "error"}
