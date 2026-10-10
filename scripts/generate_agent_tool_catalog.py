"""Generate the tool catalogue from the live registry, not a hand-maintained list."""

from __future__ import annotations

from pathlib import Path

from requirement_agent.tools.registry import descriptors, get_class


def _category(module: str) -> str:
    if ".external." in module:
        return "external"
    if ".memory." in module:
        return "memory"
    if ".documents." in module:
        return "documents"
    return "requirements"


def _dependency(module: str) -> str:
    if ".external." in module:
        return "Tavily MCP（配置、凭据、公共 URL 校验）"
    if "analysis" in module:
        return "既有 ExtractAgent / AnalyzeAgent / RiskAgent"
    return "RequirementQuery / RetrievalService（按工具实现）"


def main() -> None:
    rows = ["# Agent 工具目录\n", "此文件由 `python scripts/generate_agent_tool_catalog.py` 从实际注册表生成；请勿手工维护工具行。\n", "| 分类 | 名称 | 用途 | 输入 / 输出 | 消费者（invoker 强制） | 超时 | 依赖 | 错误与空结果语义 |", "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for item in descriptors():
        cls = get_class(item["name"])
        assert cls is not None
        module = cls.__module__
        input_name = cls.input_model.__name__
        output = item["output_schema"].get("type", "object")
        rows.append(
            f"| {_category(module)} | `{item['name']}` | {item['description']} | `{input_name}` → `{output}` | "
            f"{', '.join(item['allowed_consumers'])} | {item['timeout']}s | {_dependency(module)} | "
            "`error`=参数/依赖/超时失败；`empty`=成功查询但无结果；`success`=有结果 |"
        )
    rows.extend(["", "正式审批、落库、合并、删除、回退工具被注册表拒绝；权限由 `tools.invoker.invoke` 的 `allowed_consumers` 校验，而非目录或提示词。", ""])
    Path("docs/agent_tool_catalog.md").write_text("\n".join(rows), encoding="utf-8")


if __name__ == "__main__":
    main()
