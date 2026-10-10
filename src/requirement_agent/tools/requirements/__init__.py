"""Requirement-domain tools.

Legacy public modules remain at ``requirement_agent.tools.*`` for import
compatibility.  This package is the capability namespace and registers the
structured adapters for the pre-existing extraction, relation and risk agents.
"""

from requirement_agent.tools.requirements.analysis import (  # noqa: F401
    AssessRequirementRiskTool,
    ExtractRequirementTool,
    AnalyzeRequirementRelationTool,
)
