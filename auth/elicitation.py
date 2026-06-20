from __future__ import annotations

from pydantic import BaseModel

from mcp import types as mcp_types


class DestructiveApprovalForm(BaseModel):
    confirm: bool
    reason: str | None = None


def supports_form_elicitation(session: object) -> bool:
    capability = mcp_types.ClientCapabilities(
        elicitation=mcp_types.ElicitationCapability(
            form=mcp_types.FormElicitationCapability(),
        )
    )
    check = getattr(session, "check_client_capability", None)
    if callable(check):
        try:
            return bool(check(capability))
        except Exception:
            return False
    return False


def build_destructive_approval_message(tool_name: str) -> str:
    return (
        f"即将执行敏感操作：{tool_name}。\n"
        "该工具会修改系统中的数据或状态。\n"
        "请确认是否继续执行当前请求。"
    )
