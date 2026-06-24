from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class MCPRefactorArchitectureTests(unittest.TestCase):
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

    def test_http_destructive_tool_requires_elicitation_support(self):
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

        class FakeSession:
            def check_client_capability(self, capability):
                return False

        fake_ctx = SimpleNamespace(request_id="req-2", session=FakeSession())
        app = create_mcp_server(
            FakeStockMasterService(),
            FakeStockDailyService(),
            fastmcp_cls=FakeFastMCP,
            transport="streamable-http",
        )

        with patch("mcp_stock_server.server.ToolDispatcher.record_denied", autospec=True):
            result = asyncio.run(
                app.registered["upsert_stock_daily_bars"]("2026-05-26", [], fake_ctx)
            )

        self.assertEqual(result["error"]["code"], "approval_unsupported")

    def test_http_destructive_tool_executes_after_elicitation_accept(self):
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

        class FakeSession:
            def check_client_capability(self, capability):
                return True

        class FakeContext:
            def __init__(self):
                self.request_id = "req-3"
                self.session = FakeSession()

            async def elicit(self, message, schema):
                return SimpleNamespace(
                    action="accept",
                    data=SimpleNamespace(confirm=True, reason="ok"),
                )

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

    def test_after_close_tool_returns_create_task_result_when_client_supports_tasks(self):
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

            @property
            def is_task(self):
                return True

            @property
            def client_supports_tasks(self):
                return True

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

    def test_after_close_tool_accepts_extension_declared_task_capability(self):
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
                self._client_capabilities = SimpleNamespace(tasks=None, extensions={"io.modelcontextprotocol/tasks": {}})

            @property
            def client_supports_tasks(self):
                return False

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

    def test_after_close_tool_falls_back_to_sync_when_client_lacks_task_capability(self):
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
            @property
            def client_supports_tasks(self):
                return False

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
        self.assertTrue(definitions["upsert_stock_daily_bars"].destructive)
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
