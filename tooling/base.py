from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from .definitions import ToolDefinition

if TYPE_CHECKING:
    from ..auth.context import AuthContext


class BaseTool:
    definition: ToolDefinition

    def execute(self, args: dict[str, Any], context: "AuthContext") -> dict[str, Any] | list[str]:
        raise NotImplementedError


@dataclass(slots=True)
class FunctionTool(BaseTool):
    definition: ToolDefinition
    handler: Callable[[dict[str, Any], "AuthContext"], dict[str, Any] | list[str]]

    def execute(self, args: dict[str, Any], context: "AuthContext") -> dict[str, Any] | list[str]:
        return self.handler(args, context)
