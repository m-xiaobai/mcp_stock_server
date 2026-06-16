from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from ..tooling.definitions import ToolDefinition


@dataclass(slots=True)
class AuthContext:
    user_id: str
    tenant_id: str
    scopes: set[str] = field(default_factory=set)
    approval_grants: set[str] = field(default_factory=set)
    request_id: str = ""


def build_development_auth_context(tool_definitions: list["ToolDefinition"]) -> AuthContext:
    scopes: set[str] = set()
    approval_grants: set[str] = set()
    for definition in tool_definitions:
        scopes.update(definition.required_scopes)
        if definition.destructive:
            approval_grants.add(definition.name)
    return AuthContext(
        user_id="local-dev",
        tenant_id="local",
        scopes=scopes,
        approval_grants=approval_grants,
        request_id=f"dev-{uuid4().hex}",
    )
