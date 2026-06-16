from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..auth.approval import InMemoryApprovalChecker
from ..auth.context import AuthContext
from ..tooling.definitions import ToolDefinition


@dataclass(slots=True)
class AuthorizationDecision:
    allowed: bool
    reason: str | None = None
    error_code: str | None = None


class PolicyEngine:
    def __init__(self, approval_checker: InMemoryApprovalChecker) -> None:
        self._approval_checker = approval_checker

    def authorize(
        self,
        definition: ToolDefinition,
        context: AuthContext,
        args: dict[str, Any],
    ) -> AuthorizationDecision:
        missing_scopes = definition.required_scopes.difference(context.scopes)
        if missing_scopes:
            missing_scope = sorted(missing_scopes)[0]
            return AuthorizationDecision(
                allowed=False,
                reason=f"missing required scope {missing_scope}",
                error_code="forbidden",
            )
        if definition.destructive and not self._approval_checker.has_valid_approval(
            context, definition.name
        ):
            return AuthorizationDecision(
                allowed=False,
                reason=f"approval required for {definition.name}",
                error_code="approval_required",
            )
        return AuthorizationDecision(allowed=True)
