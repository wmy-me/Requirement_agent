from __future__ import annotations

from requirement_agent.tools.common.requirement_library.list_requirements_by_module import ListRequirementsByModuleTool


class _Query:
    def __init__(self, result: dict[str, object]) -> None:
        self.result = result
        self.calls: list[tuple[object, int, int, str]] = []

    def list_requirements_by_module(self, module_name, *, page, limit, status):
        self.calls.append((module_name, page, limit, status))
        return self.result


def test_module_tool_returns_pagination_not_unbounded_top_k() -> None:
    query = _Query(
        {
            "module_name": "登录",
            "scope": "module",
            "total": 21,
            "page": 2,
            "limit": 10,
            "has_more": True,
            "items": [{"requirement_key": "REQ-1", "matched_feature_count": 2}],
        }
    )
    tool = ListRequirementsByModuleTool(query)

    result = tool.run({"module_name": " 登录 ", "page": 2, "limit": 10})

    assert result.status.value == "success"
    assert result.result["total"] == 21
    assert query.calls == [("登录", 2, 10, "active")]


def test_module_tool_keeps_empty_page_distinct_from_failure() -> None:
    query = _Query(
        {
            "module_name": None,
            "scope": "unclassified_features",
            "total": 0,
            "page": 1,
            "limit": 10,
            "has_more": False,
            "items": [],
        }
    )

    result = ListRequirementsByModuleTool(query).run({})

    assert result.status.value == "empty"
    assert result.result["total"] == 0
