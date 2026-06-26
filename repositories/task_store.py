from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import anyio
from pydantic import TypeAdapter

from mcp.shared.experimental.tasks.helpers import create_task_state, is_terminal
from mcp.shared.experimental.tasks.store import TaskStore
from mcp.types import Result, Task, TaskMetadata, TaskStatus
from ..recovery import RecoveryTaskRecord

ConnectionFactory = Callable[[], Any]


class MySQLTaskStore(TaskStore):
    def __init__(self, connection_factory: ConnectionFactory, page_size: int = 10) -> None:
        self._connection_factory = connection_factory
        self._page_size = page_size
        self._update_events: dict[str, anyio.Event] = {}
        self._update_versions: dict[str, int] = {}
        self._result_adapter = TypeAdapter(Result)

    def _calculate_expiry(self, ttl_ms: int | None) -> datetime | None:
        if ttl_ms is None:
            return None
        return datetime.now(timezone.utc) + timedelta(milliseconds=ttl_ms)

    def _is_terminal_status(self, status: TaskStatus) -> bool:
        return is_terminal(status)

    def _to_db_datetime(self, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def _from_db_datetime(self, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc)
        return value.replace(tzinfo=timezone.utc)

    def _serialize_result(self, result: Result) -> str:
        return json.dumps(result.model_dump(mode="json", by_alias=True), ensure_ascii=False)

    def _deserialize_result(self, payload: Any) -> Result:
        if isinstance(payload, str):
            payload = json.loads(payload)
        return self._result_adapter.validate_python(payload)

    def _row_to_task(self, row: dict[str, Any]) -> Task:
        return Task(
            taskId=row["task_id"],
            status=row["status"],
            statusMessage=row.get("status_message"),
            createdAt=self._from_db_datetime(row["created_at"]),
            lastUpdatedAt=self._from_db_datetime(row["last_updated_at"]),
            ttl=row.get("ttl_ms"),
            pollInterval=row["poll_interval_ms"],
        )

    def _cleanup_expired_sync(self) -> None:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
DELETE FROM mcp_tasks
WHERE expires_at IS NOT NULL
  AND expires_at <= %s
""".strip(),
                    (self._to_db_datetime(datetime.now(timezone.utc)),),
                )
            if hasattr(connection, "commit"):
                connection.commit()
        except Exception:
            if hasattr(connection, "rollback"):
                connection.rollback()
            raise

    def _create_task_sync(self, metadata: TaskMetadata, task_id: str | None = None) -> Task:
        task = create_task_state(metadata, task_id)
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT task_id FROM mcp_tasks WHERE task_id = %s",
                    (task.taskId,),
                )
                if cursor.fetchone() is not None:
                    raise ValueError(f"Task with ID {task.taskId} already exists")
                cursor.execute(
                    """
INSERT INTO mcp_tasks (
    task_id,
    status,
    status_message,
    created_at,
    last_updated_at,
    ttl_ms,
    poll_interval_ms,
    expires_at,
    result_json
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
""".strip(),
                    (
                        task.taskId,
                        task.status,
                        task.statusMessage,
                        self._to_db_datetime(task.createdAt),
                        self._to_db_datetime(task.lastUpdatedAt),
                        task.ttl,
                        task.pollInterval,
                        self._to_db_datetime(self._calculate_expiry(metadata.ttl)),
                        None,
                    ),
                )
            if hasattr(connection, "commit"):
                connection.commit()
        except Exception:
            if hasattr(connection, "rollback"):
                connection.rollback()
            raise
        return Task(**task.model_dump())

    def _get_task_sync(self, task_id: str) -> Task | None:
        connection = self._connection_factory()
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM mcp_tasks WHERE task_id = %s", (task_id,))
            row = cursor.fetchone()
        if row is None:
            return None
        return self._row_to_task(row)

    def _update_task_sync(
        self,
        task_id: str,
        status: TaskStatus | None = None,
        status_message: str | None = None,
    ) -> tuple[Task, bool]:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT * FROM mcp_tasks WHERE task_id = %s", (task_id,))
                row = cursor.fetchone()
                if row is None:
                    raise ValueError(f"Task with ID {task_id} not found")

                task = self._row_to_task(row)
                if status is not None and status != task.status and self._is_terminal_status(task.status):
                    raise ValueError(f"Cannot transition from terminal status '{task.status}'")

                status_changed = status is not None and task.status != status
                if status is not None:
                    task.status = status
                if status_message is not None:
                    task.statusMessage = status_message
                task.lastUpdatedAt = datetime.now(timezone.utc)

                expires_at = row.get("expires_at")
                if status is not None and self._is_terminal_status(status) and task.ttl is not None:
                    expires_at = self._calculate_expiry(task.ttl)

                cursor.execute(
                    """
UPDATE mcp_tasks
SET status = %s,
    status_message = %s,
    last_updated_at = %s,
    expires_at = %s
WHERE task_id = %s
""".strip(),
                    (
                        task.status,
                        task.statusMessage,
                        self._to_db_datetime(task.lastUpdatedAt),
                        self._to_db_datetime(
                            self._from_db_datetime(expires_at) if isinstance(expires_at, datetime) else expires_at
                        ),
                        task_id,
                    ),
                )
            if hasattr(connection, "commit"):
                connection.commit()
        except Exception:
            if hasattr(connection, "rollback"):
                connection.rollback()
            raise
        return Task(**task.model_dump()), status_changed

    def _store_result_sync(self, task_id: str, result: Result) -> None:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT task_id FROM mcp_tasks WHERE task_id = %s", (task_id,))
                if cursor.fetchone() is None:
                    raise ValueError(f"Task with ID {task_id} not found")
                cursor.execute(
                    "UPDATE mcp_tasks SET result_json = %s WHERE task_id = %s",
                    (self._serialize_result(result), task_id),
                )
            if hasattr(connection, "commit"):
                connection.commit()
        except Exception:
            if hasattr(connection, "rollback"):
                connection.rollback()
            raise

    def _get_result_sync(self, task_id: str) -> Result | None:
        connection = self._connection_factory()
        with connection.cursor() as cursor:
            cursor.execute("SELECT result_json FROM mcp_tasks WHERE task_id = %s", (task_id,))
            row = cursor.fetchone()
        if row is None or row.get("result_json") is None:
            return None
        return self._deserialize_result(row["result_json"])

    def _list_tasks_sync(self, cursor: str | None = None) -> tuple[list[Task], str | None]:
        connection = self._connection_factory()
        with connection.cursor() as db_cursor:
            params: tuple[Any, ...] = ()
            where_clause = ""
            if cursor is not None:
                db_cursor.execute(
                    "SELECT created_at, task_id FROM mcp_tasks WHERE task_id = %s",
                    (cursor,),
                )
                cursor_row = db_cursor.fetchone()
                if cursor_row is None:
                    raise ValueError(f"Invalid cursor: {cursor}")
                where_clause = """
WHERE (created_at > %s)
   OR (created_at = %s AND task_id > %s)
""".strip()
                params = (
                    cursor_row["created_at"],
                    cursor_row["created_at"],
                    cursor_row["task_id"],
                )

            db_cursor.execute(
                f"""
SELECT *
FROM mcp_tasks
{where_clause}
ORDER BY created_at ASC, task_id ASC
LIMIT %s
""".strip(),
                params + (self._page_size + 1,),
            )
            rows = db_cursor.fetchall()

        tasks = [self._row_to_task(row) for row in rows[: self._page_size]]
        next_cursor = None
        if len(rows) > self._page_size and tasks:
            next_cursor = tasks[-1].taskId
        return tasks, next_cursor

    def _delete_task_sync(self, task_id: str) -> bool:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM mcp_tasks WHERE task_id = %s", (task_id,))
                deleted = cursor.rowcount > 0
            if hasattr(connection, "commit"):
                connection.commit()
        except Exception:
            if hasattr(connection, "rollback"):
                connection.rollback()
            raise
        return deleted

    def _ensure_schema_sync(self) -> None:
        schema_path = Path(__file__).resolve().parents[1] / "db" / "task_schema.sql"
        statements = [statement.strip() for statement in schema_path.read_text(encoding="utf-8").split(";") if statement.strip()]
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                for statement in statements:
                    cursor.execute(statement)
            if hasattr(connection, "commit"):
                connection.commit()
        except Exception:
            if hasattr(connection, "rollback"):
                connection.rollback()
            raise

    def _reconcile_orphaned_tasks_sync(self, message: str) -> int:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
UPDATE mcp_tasks
SET status = %s,
    status_message = %s,
    last_updated_at = %s
WHERE status = %s
""".strip(),
                    (
                        "failed",
                        message,
                        self._to_db_datetime(datetime.now(timezone.utc)),
                        "working",
                    ),
                )
                updated = cursor.rowcount
            if hasattr(connection, "commit"):
                connection.commit()
        except Exception:
            if hasattr(connection, "rollback"):
                connection.rollback()
            raise
        return updated

    async def ensure_schema(self) -> None:
        await anyio.to_thread.run_sync(self._ensure_schema_sync)

    async def reconcile_orphaned_tasks(self, message: str = "server restarted before task completion") -> int:
        return await anyio.to_thread.run_sync(self._reconcile_orphaned_tasks_sync, message)

    async def create_task(self, metadata: TaskMetadata, task_id: str | None = None) -> Task:
        await anyio.to_thread.run_sync(self._cleanup_expired_sync)
        return await anyio.to_thread.run_sync(self._create_task_sync, metadata, task_id)

    async def get_task(self, task_id: str) -> Task | None:
        await anyio.to_thread.run_sync(self._cleanup_expired_sync)
        return await anyio.to_thread.run_sync(self._get_task_sync, task_id)

    async def update_task(
        self,
        task_id: str,
        status: TaskStatus | None = None,
        status_message: str | None = None,
    ) -> Task:
        task, status_changed = await anyio.to_thread.run_sync(
            self._update_task_sync,
            task_id,
            status,
            status_message,
        )
        if status_changed:
            await self.notify_update(task_id)
        return task

    async def store_result(self, task_id: str, result: Result) -> None:
        await anyio.to_thread.run_sync(self._store_result_sync, task_id, result)

    async def get_result(self, task_id: str) -> Result | None:
        return await anyio.to_thread.run_sync(self._get_result_sync, task_id)

    async def list_tasks(self, cursor: str | None = None) -> tuple[list[Task], str | None]:
        await anyio.to_thread.run_sync(self._cleanup_expired_sync)
        return await anyio.to_thread.run_sync(self._list_tasks_sync, cursor)

    async def delete_task(self, task_id: str) -> bool:
        deleted = await anyio.to_thread.run_sync(self._delete_task_sync, task_id)
        self._update_events.pop(task_id, None)
        self._update_versions.pop(task_id, None)
        return deleted

    async def wait_for_update(self, task_id: str) -> None:
        if await self.get_task(task_id) is None:
            raise ValueError(f"Task with ID {task_id} not found")
        current_version = self._update_versions.get(task_id, 0)
        self._update_events[task_id] = anyio.Event()
        event = self._update_events[task_id]
        if self._update_versions.get(task_id, 0) != current_version:
            return
        await event.wait()

    async def notify_update(self, task_id: str) -> None:
        self._update_versions[task_id] = self._update_versions.get(task_id, 0) + 1
        if task_id in self._update_events:
            self._update_events[task_id].set()

    def _register_task_definition_sync(
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
    ) -> None:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
INSERT INTO mcp_task_recovery (
    task_id,
    tool_name,
    tool_args_json,
    execution_state,
    replayable,
    user_id,
    tenant_id,
    scopes_json,
    approval_grants_json,
    attempt_count,
    last_error,
    recovery_started_at,
    lease_owner,
    lease_expires_at
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON DUPLICATE KEY UPDATE
    tool_name = VALUES(tool_name),
    tool_args_json = VALUES(tool_args_json),
    execution_state = VALUES(execution_state),
    replayable = VALUES(replayable),
    user_id = VALUES(user_id),
    tenant_id = VALUES(tenant_id),
    scopes_json = VALUES(scopes_json),
    approval_grants_json = VALUES(approval_grants_json)
""".strip(),
                    (
                        task_id,
                        tool_name,
                        json.dumps(tool_args, ensure_ascii=False),
                        "queued",
                        1 if replayable else 0,
                        user_id,
                        tenant_id,
                        json.dumps(sorted(scopes), ensure_ascii=False),
                        json.dumps(sorted(approval_grants), ensure_ascii=False),
                        0,
                        None,
                        None,
                        None,
                        None,
                    ),
                )
            if hasattr(connection, "commit"):
                connection.commit()
        except Exception:
            if hasattr(connection, "rollback"):
                connection.rollback()
            raise

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
    ) -> None:
        await anyio.to_thread.run_sync(
            lambda: self._register_task_definition_sync(
                task_id=task_id,
                tool_name=tool_name,
                tool_args=tool_args,
                user_id=user_id,
                tenant_id=tenant_id,
                scopes=scopes,
                approval_grants=approval_grants,
                replayable=replayable,
            )
        )

    def _list_recoverable_tasks_sync(self) -> list[RecoveryTaskRecord]:
        connection = self._connection_factory()
        with connection.cursor() as cursor:
            cursor.execute(
                """
SELECT r.task_id, r.tool_name, r.tool_args_json, r.user_id, r.tenant_id, r.scopes_json, r.approval_grants_json, r.replayable, r.execution_state
FROM mcp_task_recovery r
INNER JOIN mcp_tasks t
  ON t.task_id = r.task_id
WHERE t.status = %s
  AND r.execution_state IN (%s, %s)
ORDER BY r.task_id ASC
""".strip(),
                ("working", "queued", "running"),
            )
            rows = cursor.fetchall()
        records: list[RecoveryTaskRecord] = []
        for row in rows:
            records.append(
                RecoveryTaskRecord(
                    task_id=row["task_id"],
                    tool_name=row["tool_name"],
                    tool_args=json.loads(row["tool_args_json"] or "{}"),
                    user_id=row["user_id"],
                    tenant_id=row["tenant_id"],
                    scopes=set(json.loads(row["scopes_json"] or "[]")),
                    approval_grants=set(json.loads(row["approval_grants_json"] or "[]")),
                    replayable=bool(row["replayable"]),
                    execution_state=row["execution_state"],
                )
            )
        return records

    async def list_recoverable_tasks(self) -> list[RecoveryTaskRecord]:
        return await anyio.to_thread.run_sync(self._list_recoverable_tasks_sync)

    def _get_task_definition_sync(self, task_id: str) -> RecoveryTaskRecord | None:
        connection = self._connection_factory()
        with connection.cursor() as cursor:
            cursor.execute(
                """
SELECT task_id, tool_name, tool_args_json, user_id, tenant_id, scopes_json, approval_grants_json, replayable, execution_state
FROM mcp_task_recovery
WHERE task_id = %s
""".strip(),
                (task_id,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return RecoveryTaskRecord(
            task_id=row["task_id"],
            tool_name=row["tool_name"],
            tool_args=json.loads(row["tool_args_json"] or "{}"),
            user_id=row["user_id"],
            tenant_id=row["tenant_id"],
            scopes=set(json.loads(row["scopes_json"] or "[]")),
            approval_grants=set(json.loads(row["approval_grants_json"] or "[]")),
            replayable=bool(row["replayable"]),
            execution_state=row["execution_state"],
        )

    async def get_task_definition(self, task_id: str) -> RecoveryTaskRecord | None:
        return await anyio.to_thread.run_sync(self._get_task_definition_sync, task_id)

    def _mark_execution_state_sync(
        self,
        task_id: str,
        *,
        execution_state: str,
        attempt_increment: bool = False,
        last_error: str | None = None,
    ) -> None:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                sql = """
UPDATE mcp_task_recovery
SET execution_state = %s,
    recovery_started_at = %s,
    last_error = %s
""".strip()
                params: list[Any] = [execution_state, self._to_db_datetime(datetime.now(timezone.utc)), last_error]
                if attempt_increment:
                    sql += ",\n    attempt_count = attempt_count + 1"
                sql += "\nWHERE task_id = %s"
                params.append(task_id)
                cursor.execute(sql, tuple(params))
            if hasattr(connection, "commit"):
                connection.commit()
        except Exception:
            if hasattr(connection, "rollback"):
                connection.rollback()
            raise

    async def mark_task_running(self, task_id: str) -> None:
        await anyio.to_thread.run_sync(
            lambda: self._mark_execution_state_sync(
                task_id,
                execution_state="running",
                attempt_increment=True,
            )
        )

    async def mark_task_completed(self, task_id: str) -> None:
        await anyio.to_thread.run_sync(
            lambda: self._mark_execution_state_sync(
                task_id,
                execution_state="completed",
            )
        )

    async def mark_task_failed(self, task_id: str, error: str) -> None:
        await anyio.to_thread.run_sync(
            lambda: self._mark_execution_state_sync(
                task_id,
                execution_state="failed",
                last_error=error,
            )
        )
