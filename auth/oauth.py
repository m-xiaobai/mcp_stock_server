from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal

import httpx
import jwt
from jwt import PyJWKClient
from mcp.server.auth.provider import AccessToken, TokenVerifier


@dataclass(slots=True)
class MCPAuthConfig:
    enabled: bool = False
    mode: str | None = None
    verification: Literal["jwt-jwks", "introspection"] | None = None
    issuer_url: str | None = None
    resource_server_url: str | None = None
    audience: str | None = None
    jwks_uri: str | None = None
    introspection_endpoint: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    required_scopes: list[str] = field(default_factory=list)
    clock_skew_seconds: int = 60
    cache_ttl_seconds: int = 300


class JwtJwksTokenVerifier(TokenVerifier):
    def __init__(self, config: MCPAuthConfig):
        self._config = config
        self._jwks_client = PyJWKClient(config.jwks_uri or "")

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
            payload = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256", "RS384", "RS512", "ES256", "ES384", "ES512"],
                issuer=self._config.issuer_url,
                audience=self._config.audience,
                leeway=self._config.clock_skew_seconds,
                options={"require": ["exp", "iss"]},
            )
        except Exception:
            return None

        scopes = _extract_scopes(payload)
        client_id = str(payload.get("sub") or payload.get("client_id") or "unknown")
        expires_at = int(payload["exp"]) if "exp" in payload else None
        resource = str(payload.get("aud")) if payload.get("aud") is not None else None
        return AccessToken(
            token=token,
            client_id=client_id,
            scopes=scopes,
            expires_at=expires_at,
            resource=resource,
        )


class IntrospectionTokenVerifier(TokenVerifier):
    def __init__(self, config: MCPAuthConfig):
        self._config = config

    async def verify_token(self, token: str) -> AccessToken | None:
        if not self._config.introspection_endpoint:
            return None

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    self._config.introspection_endpoint,
                    data={
                        "token": token,
                        "client_id": self._config.client_id or "",
                        "client_secret": self._config.client_secret or "",
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
        except Exception:
            return None

        if response.status_code != 200:
            return None

        try:
            payload = response.json()
        except Exception:
            return None

        if not payload.get("active", False):
            return None

        exp = payload.get("exp")
        if isinstance(exp, (int, float)) and exp + self._config.clock_skew_seconds < time.time():
            return None

        scopes = _extract_scopes(payload)
        client_id = str(payload.get("sub") or payload.get("client_id") or "unknown")
        resource = payload.get("aud")
        if isinstance(resource, list):
            resource = resource[0] if resource else None

        return AccessToken(
            token=token,
            client_id=client_id,
            scopes=scopes,
            expires_at=int(exp) if isinstance(exp, (int, float)) else None,
            resource=str(resource) if resource is not None else None,
        )


def build_token_verifier(config: MCPAuthConfig) -> TokenVerifier | None:
    if not config.enabled or not config.verification:
        return None
    if config.verification == "jwt-jwks":
        return JwtJwksTokenVerifier(config)
    if config.verification == "introspection":
        return IntrospectionTokenVerifier(config)
    return None


def _extract_scopes(payload: dict) -> list[str]:
    if isinstance(payload.get("scope"), str):
        return [scope for scope in payload["scope"].split() if scope]
    if isinstance(payload.get("scp"), str):
        return [scope for scope in payload["scp"].split() if scope]
    if isinstance(payload.get("scp"), list):
        return [str(scope) for scope in payload["scp"]]
    return []
