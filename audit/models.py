from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class AuditEntry:
    timestamp: float
    request_id: str
    tenant_id: str
    user_id: str
    tool_name: str
    args_redacted: dict[str, Any]
    response_redacted: dict[str, Any] | list[Any] | None
    outcome: str
    error_code: str | None
    latency_ms: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
