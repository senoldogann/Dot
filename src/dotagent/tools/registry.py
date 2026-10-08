"""The set of tools a run can use, filtered by run mode."""

from collections.abc import Sequence
from dataclasses import dataclass

from dotagent.core.errors import ConfigError
from dotagent.llm.types import ToolSpec
from dotagent.store.models import RunMode
from dotagent.tools.base import RegisteredTool


@dataclass(frozen=True)
class ToolRegistry:
    tools: dict[str, RegisteredTool]


def build_registry(groups: Sequence[Sequence[RegisteredTool]]) -> ToolRegistry:
    """Merge tool groups; a name that appears twice is a configuration error."""
    merged: dict[str, RegisteredTool] = {}
    for group in groups:
        for tool in group:
            name: str = tool.spec.name
            if name in merged:
                raise ConfigError("two tools share a name", {"name": name})
            merged[name] = tool
    return ToolRegistry(tools=merged)


def tools_for_mode(registry: ToolRegistry, mode: RunMode) -> tuple[RegisteredTool, ...]:
    return tuple(tool for tool in registry.tools.values() if mode in tool.modes)


def specs_for_mode(registry: ToolRegistry, mode: RunMode) -> tuple[ToolSpec, ...]:
    return tuple(tool.spec for tool in tools_for_mode(registry, mode))
