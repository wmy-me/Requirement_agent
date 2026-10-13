"""Explicit tool-module manifest for each Agent.

This module is the only loading entry point for tool implementations.  Keeping
the imports explicit avoids importing package helpers, tests, or unrelated
modules during registry startup.  Authorization is still enforced by
``tools.invoker`` using each tool's ``allowed_consumers`` declaration.
"""

from requirement_agent.tools.external import web  # noqa: F401
from requirement_agent.tools.requirements.analysis import analysis, get_requirement_risks  # noqa: F401
from requirement_agent.tools.requirements.previews import preview_merge_impact  # noqa: F401
from requirement_agent.tools.requirements.queries import (
    get_requirement_detail,
    get_requirement_features,
    list_requirement_relations,
    list_requirements,
    list_requirements_by_module,
)  # noqa: F401
from requirement_agent.tools.requirements.retrieval import (
    match_capabilities,
    search_by_capability,
    search_by_constraint,
    search_features,
    search_requirements,
)  # noqa: F401
from requirement_agent.tools.requirements.sources import (
    list_requirement_sources,
    trace_requirement_sources,
)  # noqa: F401
from requirement_agent.tools.requirements.versions import (
    compare_requirement_versions,
    get_requirement_versions,
)  # noqa: F401


ASSISTANT_TOOLS: tuple[str, ...] = (
    "compare_requirement_versions",
    "get_requirement_detail",
    "get_requirement_features",
    "get_requirement_risks",
    "get_requirement_versions",
    "list_requirement_relations",
    "list_requirement_sources",
    "list_requirements",
    "list_requirements_by_module",
    "match_capabilities",
    "preview_merge_impact",
    "read_web_page",
    "search_by_capability",
    "search_by_constraint",
    "search_features",
    "search_requirements",
    "trace_requirement_sources",
    "web_search",
)

ANALYSIS_TOOLS: tuple[str, ...] = (
    "analyze_requirement_relation",
    "assess_requirement_risk",
    "compare_requirement_versions",
    "extract_requirement",
    "get_requirement_detail",
    "get_requirement_features",
    "get_requirement_risks",
    "get_requirement_versions",
    "list_requirement_relations",
    "list_requirement_sources",
    "list_requirements",
    "match_capabilities",
    "preview_merge_impact",
    "search_by_capability",
    "search_by_constraint",
    "search_features",
    "search_requirements",
    "trace_requirement_sources",
)

TOOLSETS: dict[str, tuple[str, ...]] = {
    "assistant": ASSISTANT_TOOLS,
    "analysis": ANALYSIS_TOOLS,
}

