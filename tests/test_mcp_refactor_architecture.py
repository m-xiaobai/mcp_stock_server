from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


class MCPRefactorArchitectureTests(unittest.TestCase):
    def test_dispatch_in_worker_keeps_event_loop_responsive(self):
        from mcp_stock_server.server import _dispatch_in_worker

        started = threading.Event()
        release = threading.Event()

        class BlockingDispatcher:
            def dispatch(self, **kwargs):
                started.set()
                release.wait(timeout=1)
                return {"ok": True}

        async def scenario():
            task = asyncio.create_task(
                _dispatch_in_worker(
                    BlockingDispatcher(),
                    name="get_technical_snapshot",
                    args={},
                    context=SimpleNamespace(),
                )
            )
            await asyncio.to_thread(started.wait, 1)
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            release.set()
            self.assertEqual(await task, {"ok": True})

        asyncio.run(scenario())

    def test_mcp_runtime_config_parses_disabled_auth_defaults(self):
        from mcp_stock_server.main import MCPRuntimeConfig

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "mysql": {
                            "host": "127.0.0.1",
                            "port": 3306,
                            "user": "u",
                            "password": "p",
                            "database": "stocks",
                        },
                        "mcp": {
                            "transport": "streamable-http",
                            "auth": {"enabled": False},
                        },
                    }
                ),
                encoding="utf-8",
            )

            runtime_config = MCPRuntimeConfig.from_file(config_path)

        self.assertFalse(runtime_config.auth.enabled)
        self.assertIsNone(runtime_config.auth.verification)

    def test_mcp_runtime_config_parses_jwt_jwks_auth(self):
        from mcp_stock_server.main import MCPRuntimeConfig

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "mysql": {
                            "host": "127.0.0.1",
                            "port": 3306,
                            "user": "u",
                            "password": "p",
                            "database": "stocks",
                        },
                        "mcp": {
                            "transport": "streamable-http",
                            "auth": {
                                "enabled": True,
                                "mode": "resource-server",
                                "verification": "jwt-jwks",
                                "issuer_url": "https://issuer.example.com",
                                "resource_server_url": "https://api.example.com/mcp",
                                "audience": "mcp-stock-server",
                                "jwks_uri": "https://issuer.example.com/.well-known/jwks.json",
                                "required_scopes": ["stock:daily:read"],
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )

            runtime_config = MCPRuntimeConfig.from_file(config_path)

        self.assertTrue(runtime_config.auth.enabled)
        self.assertEqual(runtime_config.auth.verification, "jwt-jwks")
        self.assertEqual(runtime_config.auth.audience, "mcp-stock-server")
        self.assertEqual(runtime_config.auth.required_scopes, ["stock:daily:read"])

    def test_create_mcp_server_injects_http_auth_when_enabled(self):
        from mcp_stock_server.main import MCPAuthConfig
        from mcp_stock_server.server import create_mcp_server

        captured = {}

        class FakeFastMCP:
            def __init__(self, name, **kwargs):
                self.name = name
                self.kwargs = kwargs
                self.registered = {}
                captured.update(kwargs)

            def tool(self, name=None, description=None):
                def decorator(func):
                    self.registered[name or func.__name__] = func
                    return func

                return decorator

        class FakeStockMasterService:
            def list_stock_codes(self):
                return []

        class FakeStockDailyService:
            def get_stock_daily_bars(self, time, codes, limit=120):
                return type("Response", (), {"time": time, "items": []})()

            def upsert_stock_daily_bars(self, request):
                return type(
                    "Response",
                    (),
                    {"time": request.time, "total": 0, "success": 0, "failed": 0, "errors": []},
                )()

        auth_config = MCPAuthConfig(
            enabled=True,
            mode="resource-server",
            verification="jwt-jwks",
            issuer_url="https://issuer.example.com",
            resource_server_url="https://api.example.com/mcp",
            audience="mcp-stock-server",
            jwks_uri="https://issuer.example.com/.well-known/jwks.json",
            required_scopes=["stock:daily:read"],
        )

        create_mcp_server(
            FakeStockMasterService(),
            FakeStockDailyService(),
            fastmcp_cls=FakeFastMCP,
            transport="streamable-http",
            auth_config=auth_config,
        )

        self.assertIn("auth", captured)
        self.assertIn("token_verifier", captured)
        self.assertEqual(str(captured["auth"].issuer_url), "https://issuer.example.com/")

    def test_http_dispatch_uses_development_auth_context_after_entry_auth(self):
        from mcp_stock_server.main import MCPAuthConfig
        from mcp_stock_server.server import create_mcp_server
        from mcp_stock_server.auth.context import AuthContext
        from unittest.mock import patch

        class FakeFastMCP:
            def __init__(self, name, **kwargs):
                self.name = name
                self.registered = {}

            def tool(self, name=None, description=None):
                def decorator(func):
                    self.registered[name or func.__name__] = func
                    return func

                return decorator

        class FakeStockMasterService:
            def list_stock_codes(self):
                return []

        class FakeStockDailyService:
            def get_stock_daily_bars(self, time, codes, limit=120):
                return type("Response", (), {"time": time, "items": []})()

            def upsert_stock_daily_bars(self, request):
                return type(
                    "Response",
                    (),
                    {"time": request.time, "total": 0, "success": 0, "failed": 0, "errors": []},
                )()

        auth_config = MCPAuthConfig(
            enabled=True,
            mode="resource-server",
            verification="jwt-jwks",
            issuer_url="https://issuer.example.com",
            resource_server_url="https://api.example.com/mcp",
            audience="mcp-stock-server",
            jwks_uri="https://issuer.example.com/.well-known/jwks.json",
            required_scopes=["mcp:tools"],
        )

        app = create_mcp_server(
            FakeStockMasterService(),
            FakeStockDailyService(),
            fastmcp_cls=FakeFastMCP,
            transport="streamable-http",
            auth_config=auth_config,
        )

        captured_contexts: list[AuthContext] = []

        with (
            patch(
                "mcp_stock_server.server.get_access_token",
                return_value=SimpleNamespace(client_id="client-123"),
            ) as get_token,
            patch(
                "mcp_stock_server.server.ToolDispatcher.dispatch",
                autospec=True,
                side_effect=lambda _self, name, args, context: captured_contexts.append(context)
                or {"ok": True},
            ),
        ):
            asyncio.run(app.registered["get_stock_daily_bars"]("2026-05-26", ["000001"], SimpleNamespace(request_id="req-1")))

        self.assertEqual(get_token.call_count, 1)
        self.assertEqual(len(captured_contexts), 1)
        self.assertEqual(captured_contexts[0].user_id, "client-123")
        self.assertIn("stock:daily:read", captured_contexts[0].scopes)
        self.assertEqual(captured_contexts[0].approval_grants, set())

    def test_http_destructive_tool_no_longer_requires_elicitation_support(self):
        from mcp_stock_server.server import create_mcp_server

        class FakeFastMCP:
            def __init__(self, name, **kwargs):
                self.name = name
                self.registered = {}

            def tool(self, name=None, description=None):
                def decorator(func):
                    self.registered[name or func.__name__] = func
                    return func

                return decorator

        class FakeStockMasterService:
            def list_stock_codes(self):
                return []

        class FakeStockDailyService:
            def get_stock_daily_bars(self, time, codes, limit=120):
                return type("Response", (), {"time": time, "items": []})()

            def upsert_stock_daily_bars(self, request):
                return type(
                    "Response",
                    (),
                    {"time": request.time, "total": 0, "success": 0, "failed": 0, "errors": []},
                )()

        fake_ctx = SimpleNamespace(request_id="req-2", session=SimpleNamespace())
        app = create_mcp_server(
            FakeStockMasterService(),
            FakeStockDailyService(),
            fastmcp_cls=FakeFastMCP,
            transport="streamable-http",
        )

        with patch("mcp_stock_server.server.ToolDispatcher.record_denied", autospec=True) as record_denied:
            result = asyncio.run(
                app.registered["upsert_stock_daily_bars"]("2026-05-26", [], fake_ctx)
            )

        self.assertEqual(result["success"], 0)
        record_denied.assert_not_called()

    def test_http_destructive_tool_executes_without_elicitation_prompt(self):
        from mcp_stock_server.server import create_mcp_server
        from unittest.mock import patch

        class FakeFastMCP:
            def __init__(self, name, **kwargs):
                self.name = name
                self.registered = {}

            def tool(self, name=None, description=None):
                def decorator(func):
                    self.registered[name or func.__name__] = func
                    return func

                return decorator

        class FakeStockMasterService:
            def list_stock_codes(self):
                return []

        class FakeStockDailyService:
            def get_stock_daily_bars(self, time, codes, limit=120):
                return type("Response", (), {"time": time, "items": []})()

            def upsert_stock_daily_bars(self, request):
                return type(
                    "Response",
                    (),
                    {"time": request.time, "total": 1, "success": 1, "failed": 0, "errors": []},
                )()

        class FakeContext:
            def __init__(self):
                self.request_id = "req-3"
                self.session = SimpleNamespace()

        app = create_mcp_server(
            FakeStockMasterService(),
            FakeStockDailyService(),
            fastmcp_cls=FakeFastMCP,
            transport="streamable-http",
        )
        captured_contexts: list[AuthContext] = []

        with patch(
            "mcp_stock_server.server.ToolDispatcher.dispatch",
            autospec=True,
            side_effect=lambda _self, name, args, context: captured_contexts.append(context)
            or {"ok": True},
        ):
            result = asyncio.run(
                app.registered["upsert_stock_daily_bars"]("2026-05-26", [], FakeContext())
            )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(len(captured_contexts), 1)
        self.assertIn("upsert_stock_daily_bars", captured_contexts[0].approval_grants)

    def test_after_close_tool_returns_create_task_result_when_task_metadata_present(self):
        import mcp.types as mcp_types
        from mcp_stock_server.server import create_mcp_server

        class FakeFastMCP:
            def __init__(self, name, **kwargs):
                self.name = name
                self.registered = {}

            def tool(self, name=None, description=None):
                def decorator(func):
                    self.registered[name or func.__name__] = func
                    return func

                return decorator

        class FakeStockMasterService:
            def list_stock_codes(self):
                return []

        class FakeStockDailyService:
            def get_stock_daily_bars(self, time, codes, limit=120):
                return type("Response", (), {"time": time, "items": []})()

            def upsert_stock_daily_bars(self, request):
                return type(
                    "Response",
                    (),
                    {"time": request.time, "total": 1, "success": 1, "failed": 0, "errors": []},
                )()

        class FakeExperimental:
            def __init__(self):
                self.result = None
                self.is_task = True
                self.task_metadata = SimpleNamespace(ttl=60000)

            def validate_task_mode(self, mode, *, raise_error=True):
                return None

            async def run_task(self, work, *args, **kwargs):
                self.result = await work(SimpleNamespace())
                task = mcp_types.Task(
                    taskId="task-1",
                    status=mcp_types.TASK_STATUS_WORKING,
                    createdAt=datetime.now(timezone.utc),
                    lastUpdatedAt=datetime.now(timezone.utc),
                    ttl=60000,
                )
                return mcp_types.CreateTaskResult(task=task)

        app = create_mcp_server(
            FakeStockMasterService(),
            FakeStockDailyService(),
            fastmcp_cls=FakeFastMCP,
            transport="stdio",
        )
        experimental = FakeExperimental()
        fake_ctx = SimpleNamespace(
            request_id="req-task-1",
            request_context=SimpleNamespace(experimental=experimental),
        )

        with patch(
            "mcp_stock_server.server.ToolDispatcher.dispatch",
            autospec=True,
            return_value={"ok": True},
        ) as dispatch:
            with self.assertLogs("mcp_stock_server.server", level="INFO") as captured_logs:
                result = asyncio.run(
                    app.registered["insert_stock_daily_bars_after_close"]("2026-05-26", fake_ctx)
                )

        self.assertIsInstance(result, mcp_types.CreateTaskResult)
        self.assertEqual(dispatch.call_count, 1)
        self.assertIsInstance(experimental.result, mcp_types.CallToolResult)
        self.assertEqual(experimental.result.structuredContent, {"ok": True})
        self.assertFalse(experimental.result.isError)
        self.assertTrue(
            any("dispatching tool as task" in message for message in captured_logs.output)
        )
        self.assertTrue(any("task work started" in message for message in captured_logs.output))
        self.assertTrue(any("task work finished" in message for message in captured_logs.output))

    def test_after_close_tool_accepts_task_metadata_even_without_client_capability_flags(self):
        import mcp.types as mcp_types
        from mcp_stock_server.server import create_mcp_server

        class FakeFastMCP:
            def __init__(self, name, **kwargs):
                self.name = name
                self.registered = {}

            def tool(self, name=None, description=None):
                def decorator(func):
                    self.registered[name or func.__name__] = func
                    return func

                return decorator

        class FakeStockMasterService:
            def list_stock_codes(self):
                return []

        class FakeStockDailyService:
            def get_stock_daily_bars(self, time, codes, limit=120):
                return type("Response", (), {"time": time, "items": []})()

            def upsert_stock_daily_bars(self, request):
                return type(
                    "Response",
                    (),
                    {"time": request.time, "total": 1, "success": 1, "failed": 0, "errors": []},
                )()

        class FakeExperimental:
            def __init__(self):
                self.result = None
                self.is_task = True
                self.task_metadata = SimpleNamespace(ttl=60000)
                self._client_capabilities = SimpleNamespace(tasks=None, extensions={"io.modelcontextprotocol/tasks": {}})

            def validate_task_mode(self, mode, *, raise_error=True):
                return None

            async def run_task(self, work, *args, **kwargs):
                self.result = await work(SimpleNamespace())
                task = mcp_types.Task(
                    taskId="task-ext-1",
                    status=mcp_types.TASK_STATUS_WORKING,
                    createdAt=datetime.now(timezone.utc),
                    lastUpdatedAt=datetime.now(timezone.utc),
                    ttl=60000,
                )
                return mcp_types.CreateTaskResult(task=task)

        app = create_mcp_server(
            FakeStockMasterService(),
            FakeStockDailyService(),
            fastmcp_cls=FakeFastMCP,
            transport="stdio",
        )
        experimental = FakeExperimental()
        fake_ctx = SimpleNamespace(
            request_id="req-task-ext-1",
            request_context=SimpleNamespace(experimental=experimental),
        )

        with patch(
            "mcp_stock_server.server.ToolDispatcher.dispatch",
            autospec=True,
            return_value={"ok": True},
        ) as dispatch:
            result = asyncio.run(
                app.registered["insert_stock_daily_bars_after_close"]("2026-05-26", fake_ctx)
            )

        self.assertIsInstance(result, mcp_types.CreateTaskResult)
        self.assertEqual(dispatch.call_count, 1)
        self.assertIsInstance(experimental.result, mcp_types.CallToolResult)

    def test_after_close_tool_falls_back_to_sync_when_task_metadata_missing(self):
        from mcp_stock_server.server import create_mcp_server

        class FakeFastMCP:
            def __init__(self, name, **kwargs):
                self.name = name
                self.registered = {}

            def tool(self, name=None, description=None):
                def decorator(func):
                    self.registered[name or func.__name__] = func
                    return func

                return decorator

        class FakeStockMasterService:
            def list_stock_codes(self):
                return []

        class FakeStockDailyService:
            def get_stock_daily_bars(self, time, codes, limit=120):
                return type("Response", (), {"time": time, "items": []})()

            def upsert_stock_daily_bars(self, request):
                return type(
                    "Response",
                    (),
                    {"time": request.time, "total": 1, "success": 1, "failed": 0, "errors": []},
                )()

        class FakeExperimental:
            is_task = False
            task_metadata = None

            def validate_task_mode(self, mode, *, raise_error=True):
                return None

            async def run_task(self, work, *args, **kwargs):
                raise AssertionError("run_task should not be called when the client lacks task support")

        app = create_mcp_server(
            FakeStockMasterService(),
            FakeStockDailyService(),
            fastmcp_cls=FakeFastMCP,
            transport="stdio",
        )
        fake_ctx = SimpleNamespace(
            request_id="req-task-unsupported",
            request_context=SimpleNamespace(experimental=FakeExperimental()),
        )

        with patch(
            "mcp_stock_server.server.ToolDispatcher.dispatch",
            autospec=True,
            return_value={"ok": True},
        ) as dispatch:
            result = asyncio.run(
                app.registered["insert_stock_daily_bars_after_close"]("2026-05-26", fake_ctx)
            )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(dispatch.call_count, 1)

    def test_real_fastmcp_call_tool_skips_conversion_for_task_aware_task_requests(self):
        import mcp.types as mcp_types
        from mcp_stock_server.server import create_mcp_server

        class FakeStockMasterService:
            def list_stock_codes(self):
                return []

        class FakeStockDailyService:
            def get_stock_daily_bars(self, time, codes, limit=120):
                return type("Response", (), {"time": time, "items": []})()

            def upsert_stock_daily_bars(self, request):
                return type(
                    "Response",
                    (),
                    {"time": request.time, "total": 1, "success": 1, "failed": 0, "errors": []},
                )()

        app = create_mcp_server(
            FakeStockMasterService(),
            FakeStockDailyService(),
            transport="stdio",
        )
        class FakeExperimental:
            is_task = True
            task_metadata = SimpleNamespace(ttl=60000)

            def validate_task_mode(self, mode, *, raise_error=True):
                return None

        fake_context = SimpleNamespace(
            request_context=SimpleNamespace(experimental=FakeExperimental())
        )
        task_result = mcp_types.CreateTaskResult(
            task=mcp_types.Task(
                taskId="task-fastmcp-1",
                status=mcp_types.TASK_STATUS_WORKING,
                createdAt=datetime.now(timezone.utc),
                lastUpdatedAt=datetime.now(timezone.utc),
                ttl=60000,
            )
        )

        with patch.object(app, "get_context", return_value=fake_context):
            with patch.object(app._tool_manager, "call_tool", new_callable=AsyncMock) as call_tool:
                call_tool.return_value = task_result

                result = asyncio.run(
                    app.call_tool(
                        "get_technical_snapshot",
                        {"symbols": ["000001.SZ"], "trade_date": "2026-06-25"},
                    )
                )

        self.assertIs(result, task_result)
        self.assertEqual(call_tool.await_count, 1)
        _, kwargs = call_tool.await_args
        self.assertIs(kwargs["context"], fake_context)
        self.assertFalse(kwargs["convert_result"])

    def test_real_fastmcp_call_tool_keeps_conversion_for_non_task_requests(self):
        from mcp_stock_server.server import create_mcp_server

        class FakeStockMasterService:
            def list_stock_codes(self):
                return []

        class FakeStockDailyService:
            def get_stock_daily_bars(self, time, codes, limit=120):
                return type("Response", (), {"time": time, "items": []})()

            def upsert_stock_daily_bars(self, request):
                return type(
                    "Response",
                    (),
                    {"time": request.time, "total": 1, "success": 1, "failed": 0, "errors": []},
                )()

        app = create_mcp_server(
            FakeStockMasterService(),
            FakeStockDailyService(),
            transport="stdio",
        )
        fake_context = SimpleNamespace(
            request_context=SimpleNamespace(experimental=SimpleNamespace(is_task=False))
        )

        with patch.object(app, "get_context", return_value=fake_context):
            with patch.object(app._tool_manager, "call_tool", new_callable=AsyncMock) as call_tool:
                call_tool.return_value = {"ok": True}

                result = asyncio.run(
                    app.call_tool(
                        "get_technical_snapshot",
                        {"symbols": ["000001.SZ"], "trade_date": "2026-06-25"},
                    )
                )

        self.assertEqual(result, {"ok": True})
        _, kwargs = call_tool.await_args
        self.assertTrue(kwargs["convert_result"])

    def test_real_fastmcp_call_tool_keeps_conversion_when_task_mode_validation_fails(self):
        from mcp_stock_server.server import create_mcp_server

        class FakeStockMasterService:
            def list_stock_codes(self):
                return []

        class FakeStockDailyService:
            def get_stock_daily_bars(self, time, codes, limit=120):
                return type("Response", (), {"time": time, "items": []})()

            def upsert_stock_daily_bars(self, request):
                return type(
                    "Response",
                    (),
                    {"time": request.time, "total": 1, "success": 1, "failed": 0, "errors": []},
                )()

        class FakeExperimental:
            is_task = True
            task_metadata = SimpleNamespace(ttl=60000)

            def validate_task_mode(self, mode, *, raise_error=True):
                return object()

        app = create_mcp_server(
            FakeStockMasterService(),
            FakeStockDailyService(),
            transport="stdio",
        )
        fake_context = SimpleNamespace(
            request_context=SimpleNamespace(experimental=FakeExperimental())
        )

        with patch.object(app, "get_context", return_value=fake_context):
            with patch.object(app._tool_manager, "call_tool", new_callable=AsyncMock) as call_tool:
                call_tool.return_value = {"ok": True}

                asyncio.run(
                    app.call_tool(
                        "get_technical_snapshot",
                        {"symbols": ["000001.SZ"], "trade_date": "2026-06-25"},
                    )
                )

        _, kwargs = call_tool.await_args
        self.assertTrue(kwargs["convert_result"])

    def test_real_fastmcp_list_tools_marks_after_close_tool_task_optional(self):
        import importlib.util

        if importlib.util.find_spec("mcp") is None:
            self.skipTest("mcp package is not installed in the current test environment")

        import mcp.types as mcp_types
        from mcp_stock_server.server import create_mcp_server

        class FakeStockMasterService:
            def list_stock_codes(self):
                return []

        class FakeStockDailyService:
            def get_stock_daily_bars(self, time, codes, limit=120):
                return type("Response", (), {"time": time, "items": []})()

            def upsert_stock_daily_bars(self, request):
                return type(
                    "Response",
                    (),
                    {"time": request.time, "total": 0, "success": 0, "failed": 0, "errors": []},
                )()

        app = create_mcp_server(
            FakeStockMasterService(),
            FakeStockDailyService(),
            transport="streamable-http",
        )

        tools = asyncio.run(app.list_tools())
        tool_map = {tool.name: tool for tool in tools}

        self.assertIn("insert_stock_daily_bars_after_close", tool_map)
        self.assertIsNotNone(tool_map["insert_stock_daily_bars_after_close"].execution)
        self.assertEqual(
            tool_map["insert_stock_daily_bars_after_close"].execution.taskSupport,
            mcp_types.TASK_OPTIONAL,
        )
        self.assertIsNotNone(tool_map["upsert_stock_daily_bars"].annotations)
        self.assertFalse(tool_map["upsert_stock_daily_bars"].annotations.readOnlyHint)
        self.assertTrue(tool_map["upsert_stock_daily_bars"].annotations.destructiveHint)
        self.assertIsNotNone(tool_map["get_stock_daily_bars"].annotations)
        self.assertTrue(tool_map["get_stock_daily_bars"].annotations.readOnlyHint)
        self.assertIsNotNone(tool_map["upsert_stock_daily_bars"].meta)
        self.assertTrue(tool_map["upsert_stock_daily_bars"].meta["nanobot"]["destructive"])
        self.assertEqual(
            tool_map["upsert_stock_daily_bars"].meta["nanobot"]["requiredScopes"],
            ["stock:daily:write"],
        )

    def test_real_fastmcp_initialization_advertises_tasks_capability(self):
        import importlib.util

        if importlib.util.find_spec("mcp") is None:
            self.skipTest("mcp package is not installed in the current test environment")

        from mcp_stock_server.server import create_mcp_server

        class FakeStockMasterService:
            def list_stock_codes(self):
                return []

        class FakeStockDailyService:
            def get_stock_daily_bars(self, time, codes, limit=120):
                return type("Response", (), {"time": time, "items": []})()

            def upsert_stock_daily_bars(self, request):
                return type(
                    "Response",
                    (),
                    {"time": request.time, "total": 0, "success": 0, "failed": 0, "errors": []},
                )()

        app = create_mcp_server(
            FakeStockMasterService(),
            FakeStockDailyService(),
            transport="streamable-http",
        )

        capabilities = app._mcp_server.create_initialization_options().capabilities
        self.assertIsNotNone(capabilities.tasks)
        self.assertIsNotNone(capabilities.tasks.list)
        self.assertIsNotNone(capabilities.tasks.cancel)
        self.assertIsNotNone(capabilities.tasks.requests)
        self.assertIsNotNone(capabilities.tasks.requests.tools)
        self.assertIsNotNone(capabilities.tasks.requests.tools.call)
        self.assertIn("io.modelcontextprotocol/tasks", capabilities.extensions)

    def test_create_mcp_server_passes_custom_task_store_to_enable_tasks(self):
        from mcp.shared.experimental.tasks.message_queue import InMemoryTaskMessageQueue
        from mcp.shared.experimental.tasks.store import TaskStore
        from mcp_stock_server.server import create_mcp_server

        class FakeExperimental:
            def __init__(self):
                self.calls = []

            def enable_tasks(self, **kwargs):
                self.calls.append(kwargs)

        class FakeLowLevelServer:
            def __init__(self):
                self.experimental = FakeExperimental()

            def create_initialization_options(self, *args, **kwargs):
                return SimpleNamespace(capabilities=SimpleNamespace(extensions={}))

            def list_tools(self):
                def decorator(func):
                    return func

                return decorator

            def call_tool(self, validate_input=False):
                def decorator(func):
                    return func

                return decorator

        class FakeFastMCP:
            def __init__(self, name, **kwargs):
                self.name = name
                self.registered = {}
                self._mcp_server = FakeLowLevelServer()

            def tool(self, name=None, description=None):
                def decorator(func):
                    self.registered[name or func.__name__] = func
                    return func

                return decorator

            async def list_tools(self):
                return []

        class FakeStockMasterService:
            def list_stock_codes(self):
                return []

        class FakeStockDailyService:
            def get_stock_daily_bars(self, time, codes, limit=120):
                return type("Response", (), {"time": time, "items": []})()

            def upsert_stock_daily_bars(self, request):
                return type(
                    "Response",
                    (),
                    {"time": request.time, "total": 0, "success": 0, "failed": 0, "errors": []},
                )()

        class DummyTaskStore(TaskStore):
            async def create_task(self, metadata, task_id=None):
                raise NotImplementedError

            async def get_task(self, task_id):
                raise NotImplementedError

            async def update_task(self, task_id, status=None, status_message=None):
                raise NotImplementedError

            async def store_result(self, task_id, result):
                raise NotImplementedError

            async def get_result(self, task_id):
                raise NotImplementedError

            async def list_tasks(self, cursor=None):
                raise NotImplementedError

            async def delete_task(self, task_id):
                raise NotImplementedError

            async def wait_for_update(self, task_id):
                raise NotImplementedError

            async def notify_update(self, task_id):
                raise NotImplementedError

        custom_store = DummyTaskStore()
        app = create_mcp_server(
            FakeStockMasterService(),
            FakeStockDailyService(),
            fastmcp_cls=FakeFastMCP,
            task_store=custom_store,
        )

        self.assertEqual(len(app._mcp_server.experimental.calls), 1)
        self.assertIs(app._mcp_server.experimental.calls[0]["store"], custom_store)
        self.assertIsInstance(app._mcp_server.experimental.calls[0]["queue"], InMemoryTaskMessageQueue)

    def test_stock_tool_registry_exposes_metadata_and_destructive_flags(self):
        from mcp_stock_server.tooling.stock_tools import build_stock_tool_registry

        registry = build_stock_tool_registry(
            stock_master_service=object(),
            stock_daily_service=object(),
        )

        definitions = {definition.name: definition for definition in registry.list_tools()}

        self.assertIn("list_stock_codes", definitions)
        self.assertIn("upsert_stock_daily_bars", definitions)
        self.assertIn("insert_stock_daily_bars_after_close", definitions)
        self.assertFalse(definitions["get_stock_daily_bars"].destructive)
        self.assertFalse(definitions["get_stock_daily_bars"].replayable)
        self.assertTrue(definitions["upsert_stock_daily_bars"].destructive)
        self.assertTrue(definitions["insert_stock_daily_bars_after_close"].replayable)
        self.assertTrue(definitions["get_technical_snapshot"].replayable)
        self.assertEqual(definitions["compute_kdj"].owner, "stock-platform")
        self.assertIn("stock:indicator:read", definitions["compute_kdj"].required_scopes)

    def test_policy_engine_denies_missing_scope_and_missing_approval(self):
        from mcp_stock_server.auth.approval import InMemoryApprovalChecker
        from mcp_stock_server.auth.context import AuthContext
        from mcp_stock_server.governance.policy import PolicyEngine
        from mcp_stock_server.tooling.definitions import ToolDefinition

        definition = ToolDefinition(
            name="upsert_stock_daily_bars",
            description="write data",
            input_schema={"type": "object"},
            required_scopes={"stock:daily:write"},
            destructive=True,
            owner="stock-platform",
            version="1.0.0",
        )
        policy = PolicyEngine(approval_checker=InMemoryApprovalChecker())

        denied_by_scope = policy.authorize(
            definition=definition,
            context=AuthContext(user_id="u1", tenant_id="t1", scopes=set(), approval_grants=set()),
            args={"time": "2026-05-26"},
        )
        denied_by_approval = policy.authorize(
            definition=definition,
            context=AuthContext(
                user_id="u1",
                tenant_id="t1",
                scopes={"stock:daily:write"},
                approval_grants=set(),
            ),
            args={"time": "2026-05-26"},
        )
        allowed = policy.authorize(
            definition=definition,
            context=AuthContext(
                user_id="u1",
                tenant_id="t1",
                scopes={"stock:daily:write"},
                approval_grants={"upsert_stock_daily_bars"},
            ),
            args={"time": "2026-05-26"},
        )

        self.assertFalse(denied_by_scope.allowed)
        self.assertEqual(denied_by_scope.error_code, "forbidden")
        self.assertFalse(denied_by_approval.allowed)
        self.assertEqual(denied_by_approval.error_code, "approval_required")
        self.assertTrue(allowed.allowed)

    def test_manifest_is_generated_from_registry(self):
        from mcp_stock_server.manifest.capabilities import build_capability_manifest
        from mcp_stock_server.tooling.stock_tools import build_stock_tool_registry

        registry = build_stock_tool_registry(
            stock_master_service=object(),
            stock_daily_service=object(),
        )

        manifest = build_capability_manifest(
            registry=registry,
            server_name="mcp-stock-server",
            version="1.0.0",
            transport="stdio",
            tasks_enabled=True,
            task_aware_tools=["insert_stock_daily_bars_after_close"],
        )

        self.assertEqual(manifest["server"], "mcp-stock-server")
        self.assertEqual(manifest["transport"], "stdio")
        self.assertTrue(manifest["tasks"]["enabled"])
        self.assertEqual(manifest["tasks"]["task_aware_tools"], ["insert_stock_daily_bars_after_close"])
        self.assertEqual(len(manifest["tools"]), len(registry.list_tools()))
        destructive_map = {tool["name"]: tool["destructive"] for tool in manifest["tools"]}
        self.assertTrue(destructive_map["upsert_stock_daily_bars"])
        self.assertFalse(destructive_map["list_stock_codes"])

    def test_dispatcher_executes_tool_and_writes_audit_entry(self):
        from mcp_stock_server.audit.writer import JsonlAuditWriter
        from mcp_stock_server.auth.approval import InMemoryApprovalChecker
        from mcp_stock_server.auth.context import AuthContext
        from mcp_stock_server.governance.policy import PolicyEngine
        from mcp_stock_server.governance.redaction import Redactor
        from mcp_stock_server.protocol.dispatcher import ToolDispatcher
        from mcp_stock_server.tooling.base import FunctionTool
        from mcp_stock_server.tooling.definitions import ToolDefinition
        from mcp_stock_server.tooling.registry import ToolRegistry

        definition = ToolDefinition(
            name="list_stock_codes",
            description="List codes",
            input_schema={"type": "object", "properties": {}, "required": []},
            required_scopes={"stock:master:read"},
            destructive=False,
            owner="stock-platform",
            version="1.0.0",
        )
        registry = ToolRegistry()
        registry.register(
            FunctionTool(
                definition=definition,
                handler=lambda args, context: {"items": ["000001"], "contact": "user@example.com"},
            )
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            audit_path = Path(temp_dir) / "audit.jsonl"
            dispatcher = ToolDispatcher(
                registry=registry,
                policy_engine=PolicyEngine(approval_checker=InMemoryApprovalChecker()),
                audit_writer=JsonlAuditWriter(audit_path),
                redactor=Redactor(),
            )
            payload = dispatcher.dispatch(
                name="list_stock_codes",
                args={},
                context=AuthContext(
                    user_id="u1",
                    tenant_id="t1",
                    scopes={"stock:master:read"},
                    approval_grants=set(),
                    request_id="req-1",
                ),
            )

            self.assertEqual(payload["items"], ["000001"])
            lines = audit_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            entry = json.loads(lines[0])
            self.assertEqual(entry["tool_name"], "list_stock_codes")
            self.assertEqual(entry["outcome"], "allowed")
            self.assertEqual(entry["response_redacted"]["contact"], "[REDACTED_EMAIL]")

    def test_create_mcp_server_registers_metadata_from_registry(self):
        from mcp_stock_server.server import create_mcp_server

        class FakeFastMCP:
            def __init__(self, name):
                self.name = name
                self.registered = {}

            def tool(self, name=None, description=None):
                def decorator(func):
                    self.registered[name or func.__name__] = {
                        "func": func,
                        "description": description,
                    }
                    return func

                return decorator

        class FakeStockMasterService:
            def list_stock_codes(self):
                return []

        class FakeStockDailyService:
            def get_stock_daily_bars(self, time, codes, limit=120):
                return type("Response", (), {"time": time, "items": []})()

            def upsert_stock_daily_bars(self, request):
                return type(
                    "Response",
                    (),
                    {"time": request.time, "total": 0, "success": 0, "failed": 0, "errors": []},
                )()

        app = create_mcp_server(
            FakeStockMasterService(),
            FakeStockDailyService(),
            fastmcp_cls=FakeFastMCP,
        )

        self.assertIn("get_stock_daily_bars", app.registered)
        self.assertEqual(
            app.registered["upsert_stock_daily_bars"]["description"],
            "Insert or update stock daily bars after market close.",
        )

    def test_task_request_registers_recovery_metadata_and_reuses_explicit_task_id(self):
        import mcp.types as mcp_types
        from mcp_stock_server.server import create_mcp_server

        class FakeRecoveryCoordinator:
            def __init__(self):
                self.registered = []
                self.running = []
                self.completed = []
                self.failed = []

            async def register_task_definition(self, **kwargs):
                self.registered.append(kwargs)

            async def mark_task_running(self, task_id):
                self.running.append(task_id)

            async def mark_task_completed(self, task_id):
                self.completed.append(task_id)

            async def mark_task_failed(self, task_id, error):
                self.failed.append((task_id, error))

        class FakeFastMCP:
            def __init__(self, name, **kwargs):
                self.name = name
                self.registered = {}

            def tool(self, name=None, description=None):
                def decorator(func):
                    self.registered[name or func.__name__] = func
                    return func

                return decorator

        class FakeStockMasterService:
            def list_stock_codes(self):
                return []

        class FakeStockDailyService:
            def get_stock_daily_bars(self, time, codes, limit=120):
                return type("Response", (), {"time": time, "items": []})()

            def upsert_stock_daily_bars(self, request):
                return type(
                    "Response",
                    (),
                    {"time": request.time, "total": 1, "success": 1, "failed": 0, "errors": []},
                )()

        class FakeExperimental:
            def __init__(self):
                self.task_metadata = SimpleNamespace(ttl=60000)
                self.is_task = True
                self.captured_task_id = None

            def validate_task_mode(self, mode, *, raise_error=True):
                return None

            async def run_task(self, work, *, task_id=None, **kwargs):
                self.captured_task_id = task_id
                await work(SimpleNamespace())
                task = mcp_types.Task(
                    taskId=task_id,
                    status=mcp_types.TASK_STATUS_WORKING,
                    createdAt=datetime.now(timezone.utc),
                    lastUpdatedAt=datetime.now(timezone.utc),
                    ttl=60000,
                )
                return mcp_types.CreateTaskResult(task=task)

        recovery = FakeRecoveryCoordinator()
        app = create_mcp_server(
            FakeStockMasterService(),
            FakeStockDailyService(),
            fastmcp_cls=FakeFastMCP,
            recovery_coordinator=recovery,
        )
        experimental = FakeExperimental()
        fake_ctx = SimpleNamespace(
            request_id="req-task-replay-1",
            request_context=SimpleNamespace(experimental=experimental),
        )

        with patch(
            "mcp_stock_server.server.ToolDispatcher.dispatch",
            autospec=True,
            return_value={"ok": True},
        ):
            result = asyncio.run(
                app.registered["insert_stock_daily_bars_after_close"]("2026-05-26", fake_ctx)
            )

        self.assertIsInstance(result, mcp_types.CreateTaskResult)
        self.assertIsNotNone(experimental.captured_task_id)
        self.assertEqual(result.task.taskId, experimental.captured_task_id)
        self.assertEqual(len(recovery.registered), 1)
        self.assertEqual(recovery.registered[0]["tool_name"], "insert_stock_daily_bars_after_close")
        self.assertEqual(recovery.registered[0]["tool_args"], {"time": "2026-05-26"})
        self.assertTrue(recovery.registered[0]["replayable"])
        self.assertEqual(recovery.running, [result.task.taskId])
        self.assertEqual(recovery.completed, [result.task.taskId])

        with patch(
            "mcp_stock_server.server.ToolDispatcher.dispatch",
            autospec=True,
            side_effect=RuntimeError("dispatcher failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "dispatcher failed"):
                asyncio.run(
                    app.registered["insert_stock_daily_bars_after_close"](
                        "2026-05-26", fake_ctx
                    )
                )

        failed_task_id = experimental.captured_task_id
        self.assertEqual(recovery.running[-1], failed_task_id)
        self.assertEqual(recovery.failed[-1], (failed_task_id, "dispatcher failed"))
        self.assertNotIn(failed_task_id, recovery.completed)

    def test_create_mcp_server_wraps_lifespan_to_schedule_recovery(self):
        from contextlib import asynccontextmanager
        from mcp_stock_server.server import create_mcp_server

        class FakeRecoveryCoordinator:
            def __init__(self):
                self.calls = 0

            async def schedule_recovery_on_startup(self, task_group):
                self.calls += 1
                return 0

        class FakeLowLevelServer:
            def __init__(self):
                self.experimental = SimpleNamespace(enable_tasks=lambda **kwargs: None)

            @asynccontextmanager
            async def lifespan(self, _server):
                yield {"ok": True}

            def create_initialization_options(self, *args, **kwargs):
                return SimpleNamespace(capabilities=SimpleNamespace(extensions={}))

            def list_tools(self):
                def decorator(func):
                    return func

                return decorator

            def call_tool(self, validate_input=False):
                def decorator(func):
                    return func

                return decorator

        class FakeFastMCP:
            def __init__(self, name, **kwargs):
                self.name = name
                self.registered = {}
                self._mcp_server = FakeLowLevelServer()

            def tool(self, name=None, description=None):
                def decorator(func):
                    self.registered[name or func.__name__] = func
                    return func

                return decorator

            async def list_tools(self):
                return []

        class FakeStockMasterService:
            def list_stock_codes(self):
                return []

        class FakeStockDailyService:
            def get_stock_daily_bars(self, time, codes, limit=120):
                return type("Response", (), {"time": time, "items": []})()

            def upsert_stock_daily_bars(self, request):
                return type(
                    "Response",
                    (),
                    {"time": request.time, "total": 0, "success": 0, "failed": 0, "errors": []},
                )()

        recovery = FakeRecoveryCoordinator()
        app = create_mcp_server(
            FakeStockMasterService(),
            FakeStockDailyService(),
            fastmcp_cls=FakeFastMCP,
            recovery_coordinator=recovery,
        )

        async def run_lifespan():
            async with app._mcp_server.lifespan(app._mcp_server) as context:
                self.assertEqual(context, {"ok": True})

        asyncio.run(run_lifespan())
        self.assertEqual(recovery.calls, 1)

    def test_create_mcp_server_can_disable_automatic_recovery(self):
        from mcp_stock_server.server import create_mcp_server

        class FakeRecoveryCapableStore:
            async def register_task_definition(self, **kwargs):
                raise NotImplementedError

        class FakeLowLevelServer:
            def __init__(self):
                self.experimental = SimpleNamespace(enable_tasks=lambda **kwargs: None)
                self.lifespan = lambda _server: None

            def create_initialization_options(self, *args, **kwargs):
                return SimpleNamespace(capabilities=SimpleNamespace(extensions={}))

            def list_tools(self):
                def decorator(func):
                    return func

                return decorator

            def call_tool(self, validate_input=False):
                def decorator(func):
                    return func

                return decorator

        class FakeFastMCP:
            def __init__(self, name, **kwargs):
                self.name = name
                self.registered = {}
                self._mcp_server = FakeLowLevelServer()

            def tool(self, name=None, description=None):
                def decorator(func):
                    self.registered[name or func.__name__] = func
                    return func

                return decorator

            async def list_tools(self):
                return []

        class FakeStockMasterService:
            def list_stock_codes(self):
                return []

        class FakeStockDailyService:
            def get_stock_daily_bars(self, time, codes, limit=120):
                return type("Response", (), {"time": time, "items": []})()

            def upsert_stock_daily_bars(self, request):
                return type(
                    "Response",
                    (),
                    {"time": request.time, "total": 0, "success": 0, "failed": 0, "errors": []},
                )()

        app = create_mcp_server(
            FakeStockMasterService(),
            FakeStockDailyService(),
            fastmcp_cls=FakeFastMCP,
            task_store=FakeRecoveryCapableStore(),
            recovery_enabled=False,
        )

        self.assertIsNone(app.task_recovery_coordinator)

    def test_dispatcher_rejects_wrong_argument_type(self):
        from mcp_stock_server.audit.writer import JsonlAuditWriter
        from mcp_stock_server.auth.approval import InMemoryApprovalChecker
        from mcp_stock_server.auth.context import AuthContext
        from mcp_stock_server.governance.policy import PolicyEngine
        from mcp_stock_server.governance.redaction import Redactor
        from mcp_stock_server.protocol.dispatcher import ToolDispatcher
        from mcp_stock_server.protocol.errors import ToolDispatchError
        from mcp_stock_server.tooling.base import FunctionTool
        from mcp_stock_server.tooling.definitions import ToolDefinition
        from mcp_stock_server.tooling.registry import ToolRegistry

        registry = ToolRegistry()
        registry.register(
            FunctionTool(
                definition=ToolDefinition(
                    name="get_stock_daily_bars",
                    description="Get bars",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "time": {"type": "string"},
                            "codes": {"type": "array"},
                        },
                        "required": ["time", "codes"],
                    },
                    required_scopes={"stock:daily:read"},
                    destructive=False,
                    owner="stock-platform",
                    version="1.0.0",
                ),
                handler=lambda args, context: {"ok": True},
            )
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            dispatcher = ToolDispatcher(
                registry=registry,
                policy_engine=PolicyEngine(approval_checker=InMemoryApprovalChecker()),
                audit_writer=JsonlAuditWriter(Path(temp_dir) / "audit.jsonl"),
                redactor=Redactor(),
            )
            with self.assertRaises(ToolDispatchError) as exc_info:
                dispatcher.dispatch(
                    name="get_stock_daily_bars",
                    args={"time": "2026-05-26", "codes": "000001"},
                    context=AuthContext(
                        user_id="u1",
                        tenant_id="t1",
                        scopes={"stock:daily:read"},
                        approval_grants=set(),
                        request_id="req-2",
                    ),
                )

        self.assertEqual(exc_info.exception.code, "invalid_arguments")

    def test_create_mcp_server_registers_capability_manifest_tool(self):
        from mcp_stock_server.server import create_mcp_server

        class FakeFastMCP:
            def __init__(self, name):
                self.name = name
                self.registered = {}

            def tool(self, name=None, description=None):
                def decorator(func):
                    self.registered[name or func.__name__] = func
                    return func

                return decorator

        class FakeStockMasterService:
            def list_stock_codes(self):
                return []

        class FakeStockDailyService:
            def get_stock_daily_bars(self, time, codes, limit=120):
                return type("Response", (), {"time": time, "items": []})()

            def upsert_stock_daily_bars(self, request):
                return type(
                    "Response",
                    (),
                    {"time": request.time, "total": 0, "success": 0, "failed": 0, "errors": []},
                )()

        app = create_mcp_server(
            FakeStockMasterService(),
            FakeStockDailyService(),
            fastmcp_cls=FakeFastMCP,
        )

        self.assertIn("get_capability_manifest", app.registered)
        payload = app.registered["get_capability_manifest"]()
        self.assertEqual(payload["server"], "mcp-stock-server")
        self.assertIn("tools", payload)


if __name__ == "__main__":
    unittest.main()
