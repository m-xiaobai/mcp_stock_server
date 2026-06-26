from __future__ import annotations

import unittest
from dataclasses import dataclass

import anyio


@dataclass(slots=True)
class FakeRecoveryRecord:
    task_id: str
    tool_name: str
    tool_args: dict[str, object]
    user_id: str
    tenant_id: str
    scopes: set[str]
    approval_grants: set[str]
    replayable: bool
    execution_state: str


class TaskRecoveryCoordinatorTests(unittest.TestCase):
    def test_recovery_coordinator_replays_recoverable_tasks_and_fails_non_replayable(self):
        from mcp_stock_server.recovery import TaskRecoveryCoordinator

        class FakeTaskDefinitionStore:
            def __init__(self):
                self.records = [
                    FakeRecoveryRecord(
                        task_id="task-recover",
                        tool_name="insert_stock_daily_bars_after_close",
                        tool_args={"time": "2026-05-26"},
                        user_id="u1",
                        tenant_id="t1",
                        scopes={"stock:daily:write"},
                        approval_grants={"insert_stock_daily_bars_after_close"},
                        replayable=True,
                        execution_state="running",
                    ),
                    FakeRecoveryRecord(
                        task_id="task-no-recover",
                        tool_name="future_tool",
                        tool_args={"x": 1},
                        user_id="u2",
                        tenant_id="t2",
                        scopes={"scope:x"},
                        approval_grants=set(),
                        replayable=False,
                        execution_state="queued",
                    ),
                ]
                self.marked_running = []
                self.marked_completed = []
                self.marked_failed = []

            async def list_recoverable_tasks(self):
                return list(self.records)

            async def get_task_definition(self, task_id: str):
                for record in self.records:
                    if record.task_id == task_id:
                        return record
                return None

            async def mark_task_running(self, task_id: str):
                self.marked_running.append(task_id)

            async def mark_task_completed(self, task_id: str):
                self.marked_completed.append(task_id)

            async def mark_task_failed(self, task_id: str, error: str):
                self.marked_failed.append((task_id, error))

        class FakeTaskStore:
            def __init__(self):
                self.stored_results = []
                self.updated = []

            async def store_result(self, task_id, result):
                self.stored_results.append((task_id, result))

            async def update_task(self, task_id, status=None, status_message=None):
                self.updated.append((task_id, status, status_message))

        async def scenario():
            definition_store = FakeTaskDefinitionStore()
            task_store = FakeTaskStore()
            replayed = []

            async def execute(record):
                replayed.append(record.task_id)
                return {"ok": True, "task_id": record.task_id}

            coordinator = TaskRecoveryCoordinator(
                task_store=task_store,
                definition_store=definition_store,
                execute_record=execute,
            )

            async with anyio.create_task_group() as tg:
                count = await coordinator.schedule_recovery_on_startup(tg)

            self.assertEqual(count, 1)
            self.assertEqual(replayed, ["task-recover"])
            self.assertEqual(definition_store.marked_running, ["task-recover"])
            self.assertEqual(definition_store.marked_completed, ["task-recover"])
            self.assertEqual(len(task_store.stored_results), 1)
            update_map = {task_id: (status, status_message) for task_id, status, status_message in task_store.updated}
            self.assertEqual(update_map["task-recover"][0], "completed")
            self.assertEqual(update_map["task-no-recover"][0], "failed")
            self.assertEqual(
                definition_store.marked_failed,
                [("task-no-recover", "task cannot be replayed after server restart")],
            )

        anyio.run(scenario)

    def test_replay_task_reuses_existing_task_id_and_rejects_non_replayable(self):
        from mcp_stock_server.recovery import TaskRecoveryCoordinator

        class FakeTaskDefinitionStore:
            def __init__(self):
                self.records = {
                    "task-yes": FakeRecoveryRecord(
                        task_id="task-yes",
                        tool_name="get_technical_snapshot",
                        tool_args={"symbols": ["600000"], "trade_date": "2026-05-26"},
                        user_id="u1",
                        tenant_id="t1",
                        scopes={"stock:snapshot:read"},
                        approval_grants=set(),
                        replayable=True,
                        execution_state="queued",
                    ),
                    "task-no": FakeRecoveryRecord(
                        task_id="task-no",
                        tool_name="future_tool",
                        tool_args={"x": 1},
                        user_id="u2",
                        tenant_id="t2",
                        scopes={"scope:x"},
                        approval_grants=set(),
                        replayable=False,
                        execution_state="queued",
                    ),
                }
                self.marked_running = []
                self.marked_completed = []
                self.marked_failed = []

            async def register_task_definition(self, **kwargs):
                raise NotImplementedError

            async def list_recoverable_tasks(self):
                return list(self.records.values())

            async def get_task_definition(self, task_id: str):
                return self.records.get(task_id)

            async def mark_task_running(self, task_id: str):
                self.marked_running.append(task_id)

            async def mark_task_completed(self, task_id: str):
                self.marked_completed.append(task_id)

            async def mark_task_failed(self, task_id: str, error: str):
                self.marked_failed.append((task_id, error))

        class FakeTaskStore:
            def __init__(self):
                self.stored_results = []
                self.updated = []

            async def store_result(self, task_id, result):
                self.stored_results.append((task_id, result))

            async def update_task(self, task_id, status=None, status_message=None):
                self.updated.append((task_id, status, status_message))

        async def scenario():
            definition_store = FakeTaskDefinitionStore()
            task_store = FakeTaskStore()
            replayed = []

            async def execute(record):
                replayed.append(record.task_id)
                return {"ok": True}

            coordinator = TaskRecoveryCoordinator(
                task_store=task_store,
                definition_store=definition_store,
                execute_record=execute,
            )

            self.assertTrue(await coordinator.replay_task("task-yes"))
            self.assertFalse(await coordinator.replay_task("task-no"))
            self.assertEqual(replayed, ["task-yes"])
            self.assertEqual(definition_store.marked_running, ["task-yes"])
            self.assertEqual(definition_store.marked_completed, ["task-yes"])

        anyio.run(scenario)
