from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

import mcp.types as mcp_types
from anyio.abc import TaskGroup


@dataclass(slots=True)
class RecoveryTaskRecord:
    task_id: str
    tool_name: str
    tool_args: dict[str, Any]
    user_id: str
    tenant_id: str
    scopes: set[str]
    approval_grants: set[str]
    replayable: bool
    execution_state: str


class RecoveryDefinitionStore(Protocol):
    async def register_task_definition(
        self,
        *,
        task_id: str,
        tool_name: str,
        tool_args: dict[str, Any],
        user_id: str,
        tenant_id: str,
        scopes: set[str],
        approval_grants: set[str],
        replayable: bool,
    ) -> None: ...

    async def list_recoverable_tasks(self) -> list[RecoveryTaskRecord]: ...

    async def get_task_definition(self, task_id: str) -> RecoveryTaskRecord | None: ...

    async def mark_task_running(self, task_id: str) -> None: ...

    async def mark_task_completed(self, task_id: str) -> None: ...

    async def mark_task_failed(self, task_id: str, error: str) -> None: ...


class RecoveryResultStore(Protocol):
    async def store_result(self, task_id: str, result: mcp_types.Result) -> None: ...

    async def update_task(
        self,
        task_id: str,
        status: str | None = None,
        status_message: str | None = None,
    ) -> Any: ...


class TaskRecoveryCoordinator:
    def __init__(
        self,
        *,
        task_store: RecoveryResultStore,
        definition_store: RecoveryDefinitionStore,
        execute_record: Callable[[RecoveryTaskRecord], Awaitable[dict[str, Any] | list[str] | mcp_types.Result]],
    ) -> None:
        self._task_store = task_store
        self._definition_store = definition_store
        self._execute_record = execute_record

    async def register_task_definition(self, **kwargs: Any) -> None:
        await self._definition_store.register_task_definition(**kwargs)

    async def mark_task_running(self, task_id: str) -> None:
        await self._definition_store.mark_task_running(task_id)

    async def mark_task_completed(self, task_id: str) -> None:
        await self._definition_store.mark_task_completed(task_id)

    async def mark_task_failed(self, task_id: str, error: str) -> None:
        await self._definition_store.mark_task_failed(task_id, error)

    async def replay_task(self, task_id: str) -> bool:
        record = await self._definition_store.get_task_definition(task_id)
        if record is None or not record.replayable:
            return False
        await self._replay_record(record)
        return True

    async def _replay_record(self, record: RecoveryTaskRecord) -> None:
        await self._definition_store.mark_task_running(record.task_id)
        try:
            result = await self._execute_record(record)
            if not isinstance(result, mcp_types.CallToolResult):
                if isinstance(result, dict):
                    result = mcp_types.CallToolResult(
                        content=[mcp_types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))],
                        structuredContent=result,
                        isError=False,
                    )
                elif isinstance(result, list):
                    result = mcp_types.CallToolResult(
                        content=[mcp_types.TextContent(type="text", text=str(result))],
                        isError=False,
                    )
            await self._task_store.store_result(record.task_id, result)
            await self._task_store.update_task(record.task_id, status=mcp_types.TASK_STATUS_COMPLETED)
            await self._definition_store.mark_task_completed(record.task_id)
        except Exception as exc:
            await self._task_store.update_task(
                record.task_id,
                status=mcp_types.TASK_STATUS_FAILED,
                status_message=str(exc),
            )
            await self._definition_store.mark_task_failed(record.task_id, str(exc))

    async def schedule_recovery_on_startup(self, task_group: TaskGroup) -> int:
        recoverable = 0
        for record in await self._definition_store.list_recoverable_tasks():
            if not record.replayable:
                await self._task_store.update_task(
                    record.task_id,
                    status=mcp_types.TASK_STATUS_FAILED,
                    status_message="task cannot be replayed after server restart",
                )
                await self._definition_store.mark_task_failed(
                    record.task_id,
                    "task cannot be replayed after server restart",
                )
                continue
            recoverable += 1
            task_group.start_soon(self._replay_record, record)
        return recoverable
