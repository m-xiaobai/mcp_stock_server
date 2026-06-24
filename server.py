from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

import mcp.types as mcp_types
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import Context
from mcp.server.auth.middleware.auth_context import get_access_token

from .audit.writer import JsonlAuditWriter
from .auth.approval import InMemoryApprovalChecker
from .auth.context import build_development_auth_context, build_runtime_auth_context
from .auth.elicitation import (
    DestructiveApprovalForm,
    build_destructive_approval_message,
    supports_form_elicitation,
)
from .auth.oauth import MCPAuthConfig, build_token_verifier
from .governance.policy import PolicyEngine
from .governance.redaction import Redactor
from .manifest.capabilities import build_capability_manifest
from .protocol.dispatcher import ToolDispatcher
from .protocol.errors import ToolDispatchError
from .protocol.response import error_response
from .services import StockDailyService, StockMasterService
from .tooling.stock_tools import build_stock_tool_registry


logger = logging.getLogger(__name__)

TASK_AWARE_TOOLS = {"insert_stock_daily_bars_after_close","get_technical_snapshot"}
TASK_EXTENSION_NAME = "io.modelcontextprotocol/tasks"


def _to_call_tool_result(payload: Any) -> mcp_types.CallToolResult:
    if isinstance(payload, dict):
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        return mcp_types.CallToolResult(
            content=[mcp_types.TextContent(type="text", text=text)],
            structuredContent=payload,
            isError=False,
        )
    if isinstance(payload, list):
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        return mcp_types.CallToolResult(
            content=[mcp_types.TextContent(type="text", text=text)],
            isError=False,
        )
    return mcp_types.CallToolResult(
        content=[mcp_types.TextContent(type="text", text=str(payload))],
        isError=False,
    )


def _build_fastmcp_app(
    fastmcp_cls: type[Any],
    *,
    name: str,
    transport: str,
    host: str,
    port: int,
    streamable_http_path: str,
    auth_config: MCPAuthConfig | None = None,
):
    kwargs: dict[str, Any] = {}
    if transport == "streamable-http" and auth_config and auth_config.enabled:
        from mcp.server.auth.settings import AuthSettings

        kwargs["auth"] = AuthSettings(
            issuer_url=auth_config.issuer_url,
            required_scopes=auth_config.required_scopes,
            resource_server_url=auth_config.resource_server_url,
        )
        token_verifier = build_token_verifier(auth_config)
        if token_verifier is not None:
            kwargs["token_verifier"] = token_verifier

    try:
        return fastmcp_cls(
            name,
            host=host,
            port=port,
            streamable_http_path=streamable_http_path,
            **kwargs,
        )
    except TypeError:
        return fastmcp_cls(name)


def create_mcp_server(
    stock_master_service: StockMasterService,
    stock_daily_service: StockDailyService,
    fastmcp_cls: type[Any] = FastMCP,
    *,
    transport: str = "stdio",
    host: str = "127.0.0.1",
    port: int = 8000,
    streamable_http_path: str = "/mcp",
    auth_config: MCPAuthConfig | None = None,
):
    app = _build_fastmcp_app(
        fastmcp_cls,
        name="mcp-stock-server",
        transport=transport,
        host=host,
        port=port,
        streamable_http_path=streamable_http_path,
        auth_config=auth_config,
    )
    if hasattr(app, "_mcp_server") and hasattr(app._mcp_server, "experimental"):
        app._mcp_server.experimental.enable_tasks()
        original_create_initialization_options = app._mcp_server.create_initialization_options

        def create_initialization_options_with_tasks(*args: Any, **kwargs: Any):
            options = original_create_initialization_options(*args, **kwargs)
            capabilities = options.capabilities
            extensions = dict(getattr(capabilities, "extensions", {}) or {})
            extensions[TASK_EXTENSION_NAME] = {}
            setattr(capabilities, "extensions", extensions)
            return options

        app._mcp_server.create_initialization_options = create_initialization_options_with_tasks
    registry = build_stock_tool_registry(stock_master_service, stock_daily_service)
    dispatcher = ToolDispatcher(
        registry=registry,
        policy_engine=PolicyEngine(approval_checker=InMemoryApprovalChecker()),
        audit_writer=JsonlAuditWriter(
            Path(__file__).with_name("docs") / "audit" / f"mcp-audit-{transport}.jsonl"
        ),
        redactor=Redactor(),
    )
    definitions = {definition.name: definition for definition in registry.list_tools()}

    async def dispatch_tool(
        name: str,
        args: dict[str, Any],
        ctx: Context,
        *,
        allow_task_execution: bool = False,
    ) -> Any:
        definition = definitions[name]
        try:
            if transport == "streamable-http" and auth_config and auth_config.enabled:
                # Trigger FastMCP auth context resolution; tool authorization still uses
                # the existing development-style AuthContext after entry auth succeeds.
                access_token = get_access_token()
                user_id = access_token.client_id
            else:
                user_id = "local-dev"

            if transport == "streamable-http":
                auth_context = build_runtime_auth_context(
                    registry.list_tools(),
                    user_id=user_id,
                    tenant_id="default",
                    request_id=ctx.request_id,
                    grant_destructive_approvals=False,
                )
                if definition.destructive:
                    if not supports_form_elicitation(ctx.session):
                        dispatcher.record_denied(
                            name=name,
                            args=args,
                            context=auth_context,
                            error_code="approval_unsupported",
                        )
                        return error_response(
                            "approval_unsupported",
                            f"client does not support elicitation for {name}",
                        )

                    approval = await ctx.elicit(
                        build_destructive_approval_message(name),
                        DestructiveApprovalForm,
                    )
                    if approval.action == "cancel":
                        dispatcher.record_denied(
                            name=name,
                            args=args,
                            context=auth_context,
                            error_code="approval_cancelled",
                        )
                        return error_response(
                            "approval_cancelled",
                            f"user cancelled destructive operation {name}",
                        )
                    if approval.action == "decline" or not approval.data.confirm:
                        dispatcher.record_denied(
                            name=name,
                            args=args,
                            context=auth_context,
                            error_code="approval_declined",
                        )
                        return error_response(
                            "approval_declined",
                            f"user declined destructive operation {name}",
                        )

                    auth_context.approval_grants.add(name)
            else:
                auth_context = build_development_auth_context(registry.list_tools())

            experimental = getattr(getattr(ctx, "request_context", None), "experimental", None)
            task_metadata = getattr(experimental, "task_metadata", None) if experimental is not None else None
            logger.info(
                "task routing gate: tool=%s allow_task_execution=%s in_task_aware=%s experimental=%s task_metadata_present=%s",
                name,
                allow_task_execution,
                name in TASK_AWARE_TOOLS,
                experimental is not None,
                task_metadata is not None,
            )
            if allow_task_execution and name in TASK_AWARE_TOOLS and experimental is not None and task_metadata is not None:
                request_id = getattr(ctx, "request_id", None)
                logger.info(
                    "dispatching tool as task: tool=%s request_id=%s",
                    name,
                    request_id,
                )

                async def work(task_ctx: Any) -> mcp_types.CallToolResult:
                    logger.info(
                        "task work started: tool=%s request_id=%s",
                        name,
                        request_id,
                    )
                    result = dispatcher.dispatch(name=name, args=args, context=auth_context)
                    logger.info(
                        "task work finished: tool=%s request_id=%s",
                        name,
                        request_id,
                    )
                    return _to_call_tool_result(result)

                return await experimental.run_task(work)

            return dispatcher.dispatch(name=name, args=args, context=auth_context)
        except ToolDispatchError as exc:
            return error_response(exc.code, exc.message)

    # @app.tool(
    #     name="list_stock_codes",
    #     description=definitions["list_stock_codes"].description,
    # )
    # def list_stock_codes() -> list[str]:
    #     return dispatch_tool("list_stock_codes", {})

    @app.tool(
        name="get_stock_daily_bars",
        description=definitions["get_stock_daily_bars"].description,
    )
    async def get_stock_daily_bars(
        time: str,
        codes: list[str],
        ctx: Context,
    ) -> dict[str, Any]:
        return await dispatch_tool("get_stock_daily_bars", {"time": time, "codes": codes}, ctx)

    @app.tool(
        name="upsert_stock_daily_bars",
        description=definitions["upsert_stock_daily_bars"].description,
    )
    async def upsert_stock_daily_bars(
        time: str,
        daily_data: list[dict[str, Any]],
        ctx: Context,
    ) -> dict[str, Any]:
        return await dispatch_tool(
            "upsert_stock_daily_bars",
            {"time": time, "daily_data": daily_data},
            ctx,
        )

    @app.tool(
        name="insert_stock_daily_bars_after_close",
        description=definitions["insert_stock_daily_bars_after_close"].description,
    )
    async def insert_stock_daily_bars_after_close(time: str, ctx: Context) -> dict[str, Any] | mcp_types.CreateTaskResult:
        return await dispatch_tool(
            "insert_stock_daily_bars_after_close",
            {"time": time},
            ctx,
            allow_task_execution=True,
        )

    # @app.tool(
    #     name="compute_short_trend",
    #     description=definitions["compute_short_trend"].description,
    # )
    # def compute_short_trend(
    #     time: str,
    #     codes: list[str],
    #     period: int = 10,
    #     limit: int = 120,
    # ) -> dict[str, Any]:
    #     return dispatch_tool(
    #         "compute_short_trend",
    #         {
    #             "time": time,
    #             "codes": codes,
    #             "period": period,
    #             "limit": limit,
    #         },
    #     )

    # @app.tool(
    #     name="compute_multi_trend",
    #     description=definitions["compute_multi_trend"].description,
    # )
    # def compute_multi_trend(
    #     time: str,
    #     codes: list[str],
    #     periods: list[int] | None = None,
    #     limit: int = 120,
    # ) -> dict[str, Any]:
    #     return dispatch_tool(
    #         "compute_multi_trend",
    #         {
    #             "time": time,
    #             "codes": codes,
    #             "periods": periods,
    #             "limit": limit,
    #         },
    #     )

    # @app.tool(
    #     name="compute_kdj",
    #     description=definitions["compute_kdj"].description,
    # )
    # def compute_kdj(
    #     time: str,
    #     codes: list[str],
    #     period: int = 9,
    #     smooth_k: int = 3,
    #     smooth_d: int = 3,
    #     limit: int = 120,
    # ) -> dict[str, Any]:
    #     return dispatch_tool(
    #         "compute_kdj",
    #         {
    #             "time": time,
    #             "codes": codes,
    #             "period": period,
    #             "smooth_k": smooth_k,
    #             "smooth_d": smooth_d,
    #             "limit": limit,
    #         },
    #     )

    # @app.tool(
    #     name="compute_amplitude",
    #     description=definitions["compute_amplitude"].description,
    # )
    # def compute_amplitude(
    #     time: str,
    #     codes: list[str],
    #     limit: int = 120,
    # ) -> dict[str, Any]:
    #     return dispatch_tool("compute_amplitude", {"time": time, "codes": codes, "limit": limit})

    @app.tool(
        name="get_technical_snapshot",
        description=definitions["get_technical_snapshot"].description,
    )
    async def get_technical_snapshot(
        symbols: list[str],
        trade_date: str,
        lookback_days: int = 60,
        include_bars: bool = False,
        ctx: Context | None = None,
    ) -> dict[str, Any] | mcp_types.CreateTaskResult:
        assert ctx is not None
        return await dispatch_tool(
            "get_technical_snapshot",
            {
                "symbols": symbols,
                "trade_date": trade_date,
                "lookback_days": lookback_days,
                "include_bars": include_bars,
            },
            ctx,
            allow_task_execution=True,
        )

    @app.tool(
        name="get_capability_manifest",
        description="Return the machine-readable capability manifest for this MCP server.",
    )
    def get_capability_manifest() -> dict[str, Any]:
        return app.capability_manifest

    @app.tool(
        name="screen_b1_stocks",
        description=definitions["screen_b1_stocks"].description,
    )
    async def screen_b1_stocks(time: str, ctx: Context) -> dict[str, Any]:
        return await dispatch_tool("screen_b1_stocks", {"time": time}, ctx)

    app.tool_registry = registry
    tasks_enabled = bool(hasattr(app, "_mcp_server") and hasattr(app._mcp_server, "experimental"))
    app.capability_manifest = build_capability_manifest(
        registry=registry,
        server_name="mcp-stock-server",
        version="1.0.0",
        transport=transport,
        tasks_enabled=tasks_enabled,
        task_aware_tools=sorted(TASK_AWARE_TOOLS),
    )

    if hasattr(app, "_mcp_server"):
        original_list_tools = app.list_tools

        async def list_tools_with_task_support() -> list[mcp_types.Tool]:
            tools = await original_list_tools()
            for tool in tools:
                if tool.name in TASK_AWARE_TOOLS:
                    tool.execution = mcp_types.ToolExecution(taskSupport=mcp_types.TASK_OPTIONAL)
            return tools

        app.list_tools = list_tools_with_task_support
        app._mcp_server.list_tools()(list_tools_with_task_support)

    return app


def run_stdio_server(
    stock_master_service: StockMasterService,
    stock_daily_service: StockDailyService,
    fastmcp_cls: type[Any] = FastMCP,
) -> None:
    app = create_mcp_server(
        stock_master_service=stock_master_service,
        stock_daily_service=stock_daily_service,
        fastmcp_cls=fastmcp_cls,
        transport="stdio",
    )
    logger.info("mcp-stock-server ready on stdio")
    app.run(transport="stdio")


def run_streamable_http_server(
    stock_master_service: StockMasterService,
    stock_daily_service: StockDailyService,
    fastmcp_cls: type[Any] = FastMCP,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    streamable_http_path: str = "/mcp",
    auth_config: MCPAuthConfig | None = None,
) -> None:
    app = create_mcp_server(
        stock_master_service=stock_master_service,
        stock_daily_service=stock_daily_service,
        fastmcp_cls=fastmcp_cls,
        transport="streamable-http",
        host=host,
        port=port,
        streamable_http_path=streamable_http_path,
        auth_config=auth_config,
    )
    logger.info(
        "mcp-stock-server ready on streamable-http http://%s:%s%s",
        host,
        port,
        streamable_http_path,
    )
    app.run(transport="streamable-http")
