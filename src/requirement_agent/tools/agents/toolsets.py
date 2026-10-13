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

from requirement_agent.tools.registry import TOOLS


def _for_consumer(consumer: str) -> tuple[str, ...]:
    """Derive an Agent toolset from the tool's single permission declaration."""
    return tuple(sorted(name for name, cls in TOOLS.items() if consumer in cls.allowed_consumers))


# The module imports above are explicit; the names below are deliberately not.
# Adding a tool's ``allowed_consumers`` is enough to expose it here.
TOOLSETS: dict[str, tuple[str, ...]] = {
    consumer: _for_consumer(consumer) for consumer in ("assistant", "analysis")
}
