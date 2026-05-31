from __future__ import annotations

import sys
from typing import Any

from mcp.server.fastmcp import FastMCP

from .services import StockDailyService, StockMasterService
from .tools import (
    compute_amplitude_by_code_tool,
    compute_kdj_by_code_tool,
    compute_multi_trend_by_code_tool,
    compute_short_trend_by_code_tool,
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
    def list_stock_codes() -> list[str]:
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

    @app.tool(
        name="compute_short_trend",
        description="Load recent bars by stock code and compute Tongdaxin short trend EMA(EMA(C,10),10).",
    )
    def compute_short_trend(
        time: str,
        code: str,
        period: int = 10,
        limit: int = 120,
    ) -> dict[str, Any]:
        return compute_short_trend_by_code_tool(
            stock_daily_service=stock_daily_service,
            time=time,
            code=code,
            period=period,
            limit=limit,
        )

    @app.tool(
        name="compute_multi_trend",
        description="Load recent bars by stock code and compute Tongdaxin multi-trend baseline.",
    )
    def compute_multi_trend(
        time: str,
        code: str,
        periods: list[int] | None = None,
        limit: int = 120,
    ) -> dict[str, Any]:
        return compute_multi_trend_by_code_tool(
            stock_daily_service=stock_daily_service,
            time=time,
            code=code,
            periods=periods,
            limit=limit,
        )

    @app.tool(
        name="compute_kdj",
        description="Load recent bars by stock code and compute Tongdaxin KDJ indicator.",
    )
    def compute_kdj(
        time: str,
        codes: list[str],
        period: int = 9,
        smooth_k: int = 3,
        smooth_d: int = 3,
        limit: int = 120,
    ) -> dict[str, Any]:
        return compute_kdj_by_code_tool(
            stock_daily_service=stock_daily_service,
            time=time,
            codes=codes,
            period=period,
            smooth_k=smooth_k,
            smooth_d=smooth_d,
            limit=limit,
        )

    @app.tool(
        name="compute_amplitude",
        description="Load recent bars by stock code and compute today's B1 amplitude value.",
    )
    def compute_amplitude(
        time: str,
        codes: list[str],
        limit: int = 120,
    ) -> dict[str, Any]:
        return compute_amplitude_by_code_tool(
            stock_daily_service=stock_daily_service,
            time=time,
            codes=codes,
            limit=limit,
        )

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
