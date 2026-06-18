from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import uuid4

from mcp.server.auth.provider import AccessToken

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


def build_authenticated_auth_context(
    access_token: AccessToken,
    *,
    request_id: str,
    tenant_id: str = "default",
) -> AuthContext:
    return AuthContext(
        user_id=access_token.client_id,
        tenant_id=tenant_id,
        scopes=set(access_token.scopes),
        approval_grants=set(),
        request_id=request_id,
    )
