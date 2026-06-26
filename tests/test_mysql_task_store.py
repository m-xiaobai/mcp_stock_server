from __future__ import annotations

import asyncio
import unittest
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import anyio
from pydantic import TypeAdapter

from mcp.shared.experimental.tasks.helpers import create_task_state
from mcp.types import CallToolResult, TASK_STATUS_COMPLETED, TASK_STATUS_FAILED, Task, TaskMetadata


@dataclass
class FakeTaskBackend:
    tasks: dict[str, Task] = field(default_factory=dict)
    results: dict[str, dict] = field(default_factory=dict)
    expiries: dict[str, datetime | None] = field(default_factory=dict)
    recovery: dict[str, dict] = field(default_factory=dict)


class MySQLTaskStoreTests(unittest.TestCase):
    def test_mysql_task_store_contract_and_restart_reconciliation(self):
        from mcp_stock_server.repositories.task_store import MySQLTaskStore

        result_adapter = TypeAdapter(CallToolResult)

        class TestableMySQLTaskStore(MySQLTaskStore):
            def __init__(self, backend: FakeTaskBackend, page_size: int = 2):
                super().__init__(connection_factory=lambda: None, page_size=page_size)
                self._backend = backend

            def _cleanup_expired_sync(self) -> None:
                now = datetime.now(timezone.utc)
                expired = [
                    task_id
                    for task_id, expires_at in self._backend.expiries.items()
                    if expires_at is not None and expires_at <= now
                ]
                for task_id in expired:
                    self._backend.tasks.pop(task_id, None)
                    self._backend.results.pop(task_id, None)
                    self._backend.expiries.pop(task_id, None)

            def _create_task_sync(self, metadata: TaskMetadata, task_id: str | None = None) -> Task:
                task = create_task_state(metadata, task_id)
                if task.taskId in self._backend.tasks:
                    raise ValueError(f"Task with ID {task.taskId} already exists")
                self._backend.tasks[task.taskId] = Task(**task.model_dump())
                self._backend.expiries[task.taskId] = self._calculate_expiry(metadata.ttl)
                return Task(**task.model_dump())

            def _get_task_sync(self, task_id: str) -> Task | None:
                task = self._backend.tasks.get(task_id)
                return None if task is None else Task(**task.model_dump())

            def _update_task_sync(self, task_id: str, status=None, status_message=None):
                task = self._backend.tasks.get(task_id)
                if task is None:
                    raise ValueError(f"Task with ID {task_id} not found")
                if status is not None and status != task.status and self._is_terminal_status(task.status):
                    raise ValueError(f"Cannot transition from terminal status '{task.status}'")
                status_changed = status is not None and task.status != status
                if status is not None:
                    task.status = status
                if status_message is not None:
                    task.statusMessage = status_message
                task.lastUpdatedAt = datetime.now(timezone.utc)
                if status is not None and self._is_terminal_status(status) and task.ttl is not None:
                    self._backend.expiries[task_id] = self._calculate_expiry(task.ttl)
                self._backend.tasks[task_id] = task
                return Task(**task.model_dump()), status_changed

            def _store_result_sync(self, task_id: str, result) -> None:
                if task_id not in self._backend.tasks:
                    raise ValueError(f"Task with ID {task_id} not found")
                self._backend.results[task_id] = result.model_dump(mode="json", by_alias=True)

            def _get_result_sync(self, task_id: str):
                payload = self._backend.results.get(task_id)
                if payload is None:
                    return None
                return result_adapter.validate_python(payload)

            def _list_tasks_sync(self, cursor: str | None = None):
                items = sorted(
                    self._backend.tasks.values(),
                    key=lambda task: (task.createdAt, task.taskId),
                )
                start_index = 0
                if cursor is not None:
                    matches = [index for index, task in enumerate(items) if task.taskId == cursor]
                    if not matches:
                        raise ValueError(f"Invalid cursor: {cursor}")
                    start_index = matches[0] + 1
                page = items[start_index : start_index + self._page_size]
                next_cursor = None
                if start_index + self._page_size < len(items) and page:
                    next_cursor = page[-1].taskId
                return [Task(**task.model_dump()) for task in page], next_cursor

            def _delete_task_sync(self, task_id: str) -> bool:
                existed = task_id in self._backend.tasks
                self._backend.tasks.pop(task_id, None)
                self._backend.results.pop(task_id, None)
                self._backend.expiries.pop(task_id, None)
                return existed

            def _ensure_schema_sync(self) -> None:
                return None

            def _reconcile_orphaned_tasks_sync(self, message: str) -> int:
                updated = 0
                for task_id, task in list(self._backend.tasks.items()):
                    if task.status == "working":
                        task.status = TASK_STATUS_FAILED
                        task.statusMessage = message
                        task.lastUpdatedAt = datetime.now(timezone.utc)
                        self._backend.tasks[task_id] = task
                        updated += 1
                return updated

        async def scenario() -> None:
            backend = FakeTaskBackend()
            store = TestableMySQLTaskStore(backend)

            first = await store.create_task(TaskMetadata(ttl=5000), task_id="task-1")
            second = await store.create_task(TaskMetadata(ttl=5000), task_id="task-2")
            self.assertEqual(first.status, "working")
            self.assertEqual(second.status, "working")

            with self.assertRaises(ValueError):
                await store.create_task(TaskMetadata(ttl=5000), task_id="task-1")

            updated = await store.update_task("task-1", status_message="queued")
            self.assertEqual(updated.statusMessage, "queued")

            wait_task = asyncio.create_task(store.wait_for_update("task-1"))
            while "task-1" not in store._update_events:
                await asyncio.sleep(0)
            self.assertFalse(wait_task.done())
            completed = await store.update_task("task-1", status=TASK_STATUS_COMPLETED)
            await asyncio.wait_for(wait_task, timeout=1)
            self.assertEqual(completed.status, TASK_STATUS_COMPLETED)

            with self.assertRaises(ValueError):
                await store.update_task("task-1", status="working")

            result = CallToolResult(content=[], structuredContent={"ok": True}, isError=False)
            await store.store_result("task-1", result)
            restored = await store.get_result("task-1")
            self.assertEqual(restored.structuredContent, {"ok": True})

            page, next_cursor = await store.list_tasks()
            self.assertEqual([task.taskId for task in page], ["task-1", "task-2"])
            self.assertIsNone(next_cursor)

            with self.assertRaises(ValueError):
                await store.list_tasks(cursor="missing")

            self.assertTrue(await store.delete_task("task-2"))
            self.assertFalse(await store.delete_task("task-2"))

            orphan = await store.create_task(TaskMetadata(ttl=5000), task_id="task-3")
            self.assertEqual(orphan.status, "working")
            restarted_store = TestableMySQLTaskStore(backend)
            reconciled = await restarted_store.reconcile_orphaned_tasks("server restarted before task completion")
            self.assertEqual(reconciled, 1)
            reconciled_task = await restarted_store.get_task("task-3")
            self.assertEqual(reconciled_task.status, TASK_STATUS_FAILED)
            self.assertEqual(reconciled_task.statusMessage, "server restarted before task completion")

            expired = await restarted_store.create_task(TaskMetadata(ttl=1), task_id="task-expired")
            backend.expiries[expired.taskId] = datetime.now(timezone.utc) - timedelta(milliseconds=1)
            self.assertIsNone(await restarted_store.get_task("task-expired"))

        anyio.run(scenario)

    def test_mysql_task_store_recovery_metadata_contract(self):
        from mcp_stock_server.repositories.task_store import MySQLTaskStore

        class TestableMySQLTaskStore(MySQLTaskStore):
            def __init__(self, backend: FakeTaskBackend, page_size: int = 10):
                super().__init__(connection_factory=lambda: None, page_size=page_size)
                self._backend = backend

            def _ensure_schema_sync(self) -> None:
                return None

            def _register_task_definition_sync(
                self,
                *,
                task_id: str,
                tool_name: str,
                tool_args: dict[str, object],
                user_id: str,
                tenant_id: str,
                scopes: set[str],
                approval_grants: set[str],
                replayable: bool,
            ) -> None:
                self._backend.recovery[task_id] = {
                    "task_id": task_id,
                    "tool_name": tool_name,
                    "tool_args_json": tool_args,
                    "user_id": user_id,
                    "tenant_id": tenant_id,
                    "scopes_json": sorted(scopes),
                    "approval_grants_json": sorted(approval_grants),
                    "replayable": replayable,
                    "execution_state": "queued",
                    "attempt_count": 0,
                    "last_error": None,
                }

            def _list_recoverable_tasks_sync(self):
                from mcp_stock_server.recovery import RecoveryTaskRecord

                records = []
                for row in self._backend.recovery.values():
                    if row["execution_state"] not in {"queued", "running"}:
                        continue
                    records.append(
                        RecoveryTaskRecord(
                            task_id=row["task_id"],
                            tool_name=row["tool_name"],
                            tool_args=dict(row["tool_args_json"]),
                            user_id=row["user_id"],
                            tenant_id=row["tenant_id"],
                            scopes=set(row["scopes_json"]),
                            approval_grants=set(row["approval_grants_json"]),
                            replayable=bool(row["replayable"]),
                            execution_state=row["execution_state"],
                        )
                    )
                return records

            def _get_task_definition_sync(self, task_id: str):
                from mcp_stock_server.recovery import RecoveryTaskRecord

                row = self._backend.recovery.get(task_id)
                if row is None:
                    return None
                return RecoveryTaskRecord(
                    task_id=row["task_id"],
                    tool_name=row["tool_name"],
                    tool_args=dict(row["tool_args_json"]),
                    user_id=row["user_id"],
                    tenant_id=row["tenant_id"],
                    scopes=set(row["scopes_json"]),
                    approval_grants=set(row["approval_grants_json"]),
                    replayable=bool(row["replayable"]),
                    execution_state=row["execution_state"],
                )

            def _mark_execution_state_sync(
                self,
                task_id: str,
                *,
                execution_state: str,
                attempt_increment: bool = False,
                last_error: str | None = None,
            ) -> None:
                row = self._backend.recovery[task_id]
                row["execution_state"] = execution_state
                if attempt_increment:
                    row["attempt_count"] += 1
                if last_error is not None:
                    row["last_error"] = last_error

        async def scenario() -> None:
            backend = FakeTaskBackend()
            store = TestableMySQLTaskStore(backend)

            await store.register_task_definition(
                task_id="task-meta-1",
                tool_name="get_technical_snapshot",
                tool_args={"symbols": ["600000"], "trade_date": "2026-05-26"},
                user_id="u1",
                tenant_id="t1",
                scopes={"stock:snapshot:read"},
                approval_grants=set(),
                replayable=True,
            )
            record = await store.get_task_definition("task-meta-1")
            self.assertEqual(record.tool_name, "get_technical_snapshot")
            self.assertTrue(record.replayable)

            queued = await store.list_recoverable_tasks()
            self.assertEqual([item.task_id for item in queued], ["task-meta-1"])

            await store.mark_task_running("task-meta-1")
            running = await store.get_task_definition("task-meta-1")
            self.assertEqual(running.execution_state, "running")

            await store.mark_task_completed("task-meta-1")
            completed = await store.get_task_definition("task-meta-1")
            self.assertEqual(completed.execution_state, "completed")

            await store.mark_task_failed("task-meta-1", "boom")
            failed = await store.get_task_definition("task-meta-1")
            self.assertEqual(failed.execution_state, "failed")

        anyio.run(scenario)
