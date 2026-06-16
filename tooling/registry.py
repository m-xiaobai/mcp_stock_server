from __future__ import annotations

from .base import BaseTool
from .definitions import ToolDefinition


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.definition.name] = tool

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def list_tools(self) -> list[ToolDefinition]:
        return [tool.definition for tool in self._tools.values()]
