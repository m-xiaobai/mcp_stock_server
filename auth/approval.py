from __future__ import annotations

from .context import AuthContext


class InMemoryApprovalChecker:
    def has_valid_approval(self, context: AuthContext, tool_name: str) -> bool:
        return tool_name in context.approval_grants
