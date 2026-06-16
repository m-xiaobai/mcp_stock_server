from __future__ import annotations

from typing import Any


def success_response(payload: Any) -> Any:
    return payload


def error_response(code: str, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message}}
