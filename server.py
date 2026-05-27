from __future__ import annotations

import sys
from typing import Any

from mcp.server.fastmcp import FastMCP

from .services import StockDailyService, StockMasterService
from .tools import (
    insert_stock_daily_bars_after_close_tool,
    get_stock_daily_bars_tool,
    list_stock_codes_tool,
    upsert_stock_daily_bars_tool,
)


def create_mcp_server(
    stock_master_service: StockMasterService,
    stock_daily_service: StockDailyService,
    fastmcp_cls: type[Any] = FastMCP,
):
    app = fastmcp_cls("mcp-stock-server")

    @app.tool(
        name="list_stock_codes",
        description="Query all stock codes and names.",
    )
    def list_stock_codes() -> dict[str, Any]:
        return list_stock_codes_tool(stock_master_service)

    @app.tool(
        name="get_stock_daily_bars",
        description="Query recent 120 trading-day bars by stock codes.",
    )
    def get_stock_daily_bars(time: str, codes: list[str]) -> dict[str, Any]:
        payload = {"time": time, "codes": codes}
        return get_stock_daily_bars_tool(stock_daily_service, payload)

    @app.tool(
        name="upsert_stock_daily_bars",
        description="Insert or update stock daily bars after market close.",
    )
    def upsert_stock_daily_bars(time: str, daily_data: list[dict[str, Any]]) -> dict[str, Any]:
        payload = {"time": time, "daily_data": daily_data}
        return upsert_stock_daily_bars_tool(stock_daily_service, payload)

    @app.tool(
        name="insert_stock_daily_bars_after_close",
        description="Insert stock daily bars after market close.",
    )
    def insert_stock_daily_bars_after_close(time: str) -> dict[str, Any]:
        payload = {"time": time}
        return insert_stock_daily_bars_after_close_tool(stock_daily_service, payload)

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
    )
    print("mcp-stock-server ready on stdio", file=sys.stderr, flush=True)
    app.run()
