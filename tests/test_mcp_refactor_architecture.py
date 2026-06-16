from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


class MCPRefactorArchitectureTests(unittest.TestCase):
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
        )

        self.assertEqual(manifest["server"], "mcp-stock-server")
        self.assertEqual(manifest["transport"], "stdio")
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

        self.assertIn("list_stock_codes", app.registered)
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
