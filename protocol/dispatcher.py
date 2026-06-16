from __future__ import annotations

import time
from typing import Any

from jsonschema import ValidationError, validate

from ..audit.models import AuditEntry
from ..audit.writer import JsonlAuditWriter
from ..auth.context import AuthContext
from ..governance.policy import PolicyEngine
from ..governance.redaction import Redactor
from ..tooling.registry import ToolRegistry
from .errors import ToolDispatchError


def _validate_schema(schema: dict[str, Any], args: dict[str, Any]) -> None:
    if not schema:
        return
    try:
        validate(instance=args, schema=schema)
    except ValidationError as exc:
        raise ToolDispatchError("invalid_arguments", exc.message) from exc


class ToolDispatcher:
    def __init__(
        self,
        registry: ToolRegistry,
        policy_engine: PolicyEngine,
        audit_writer: JsonlAuditWriter,
        redactor: Redactor,
    ) -> None:
        self._registry = registry
        self._policy_engine = policy_engine
        self._audit_writer = audit_writer
        self._redactor = redactor

    def dispatch(self, name: str, args: dict[str, Any], context: AuthContext) -> Any:
        started_at = time.time()
        tool = self._registry.get(name)
        if tool is None:
            raise ToolDispatchError("tool_not_found", f"unknown tool {name}")

        _validate_schema(tool.definition.input_schema, args)
        decision = self._policy_engine.authorize(tool.definition, context, args)
        if not decision.allowed:
            self._write_audit(
                context=context,
                name=name,
                args=args,
                response=None,
                outcome="denied",
                error_code=decision.error_code,
                started_at=started_at,
            )
            raise ToolDispatchError(
                decision.error_code or "forbidden",
                decision.reason or "forbidden",
            )

        try:
            result = tool.execute(args, context)
        except Exception:
            self._write_audit(
                context=context,
                name=name,
                args=args,
                response=None,
                outcome="failed",
                error_code="backend_error",
                started_at=started_at,
            )
            raise

        redacted = self._redactor.apply(result)
        self._write_audit(
            context=context,
            name=name,
            args=args,
            response=redacted,
            outcome="allowed",
            error_code=None,
            started_at=started_at,
        )
        return result

    def _write_audit(
        self,
        context: AuthContext,
        name: str,
        args: dict[str, Any],
        response: Any,
        outcome: str,
        error_code: str | None,
        started_at: float,
    ) -> None:
        entry = AuditEntry(
            timestamp=started_at,
            request_id=context.request_id,
            tenant_id=context.tenant_id,
            user_id=context.user_id,
            tool_name=name,
            args_redacted=self._redactor.apply(args),
            response_redacted=self._redactor.apply(response),
            outcome=outcome,
            error_code=error_code,
            latency_ms=int((time.time() - started_at) * 1000),
        )
        self._audit_writer.write(entry)
