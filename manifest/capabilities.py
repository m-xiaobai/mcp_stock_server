from __future__ import annotations

from ..tooling.registry import ToolRegistry


def build_capability_manifest(
    registry: ToolRegistry,
    server_name: str,
    version: str,
    transport: str,
) -> dict[str, object]:
    return {
        "server": server_name,
        "version": version,
        "transport": transport,
        "tools": [
            {
                "name": definition.name,
                "required_scopes": sorted(definition.required_scopes),
                "destructive": definition.destructive,
                "owner": definition.owner,
                "version": definition.version,
            }
            for definition in registry.list_tools()
        ],
    }
