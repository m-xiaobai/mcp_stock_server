from __future__ import annotations

from typing import Any

from ..auth.context import AuthContext
from ..auth.scopes import (
    STOCK_DAILY_READ,
    STOCK_DAILY_WRITE,
    STOCK_INDICATOR_READ,
    STOCK_MASTER_READ,
    STOCK_SCREENER_READ,
    STOCK_SNAPSHOT_READ,
)
from ..services import StockDailyService, StockMasterService
from ..tools import (
    compute_amplitude_by_code_tool,
    compute_kdj_by_code_tool,
    compute_multi_trend_by_code_tool,
    compute_short_trend_by_code_tool,
    get_stock_daily_bars_tool,
    get_technical_snapshots_tool,
    insert_stock_daily_bars_after_close_tool,
    list_stock_codes_tool,
    screen_b1_stocks_tool,
    upsert_stock_daily_bars_tool,
)
from .base import FunctionTool
from .definitions import ToolDefinition
from .registry import ToolRegistry


OWNER = "stock-platform"
VERSION = "1.0.0"


def _object_schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


def build_stock_tool_registry(
    stock_master_service: StockMasterService,
    stock_daily_service: StockDailyService,
) -> ToolRegistry:
    registry = ToolRegistry()

    registry.register(
        FunctionTool(
            definition=ToolDefinition(
                name="list_stock_codes",
                description="Query all stock codes and names.",
                input_schema=_object_schema({}, []),
                required_scopes={STOCK_MASTER_READ},
                destructive=False,
                owner=OWNER,
                version=VERSION,
            ),
            handler=lambda args, context: list_stock_codes_tool(stock_master_service),
        )
    )
    registry.register(
        FunctionTool(
            definition=ToolDefinition(
                name="get_stock_daily_bars",
                description="Query recent 120 trading-day bars by stock codes.",
                input_schema=_object_schema(
                    {
                        "time": {"type": "string"},
                        "codes": {"type": "array"},
                    },
                    ["time", "codes"],
                ),
                required_scopes={STOCK_DAILY_READ},
                destructive=False,
                owner=OWNER,
                version=VERSION,
            ),
            handler=lambda args, context: get_stock_daily_bars_tool(stock_daily_service, args),
        )
    )
    registry.register(
        FunctionTool(
            definition=ToolDefinition(
                name="upsert_stock_daily_bars",
                description="Insert or update stock daily bars after market close.",
                input_schema=_object_schema(
                    {
                        "time": {"type": "string"},
                        "daily_data": {"type": "array"},
                    },
                    ["time", "daily_data"],
                ),
                required_scopes={STOCK_DAILY_WRITE},
                destructive=True,
                owner=OWNER,
                version=VERSION,
            ),
            handler=lambda args, context: upsert_stock_daily_bars_tool(stock_daily_service, args),
        )
    )
    registry.register(
        FunctionTool(
            definition=ToolDefinition(
                name="insert_stock_daily_bars_after_close",
                description="Insert stock daily bars after market close.",
                input_schema=_object_schema({"time": {"type": "string"}}, ["time"]),
                required_scopes={STOCK_DAILY_WRITE},
                destructive=True,
                owner=OWNER,
                version=VERSION,
                replayable=True,
            ),
            handler=lambda args, context: insert_stock_daily_bars_after_close_tool(
                stock_daily_service, args
            ),
        )
    )
    registry.register(
        FunctionTool(
            definition=ToolDefinition(
                name="compute_short_trend",
                description="Load recent bars by stock code and compute Tongdaxin short trend EMA(EMA(C,10),10).",
                input_schema=_object_schema(
                    {
                        "time": {"type": "string"},
                        "codes": {"type": "array"},
                        "period": {"type": "integer"},
                        "limit": {"type": "integer"},
                    },
                    ["time", "codes"],
                ),
                required_scopes={STOCK_INDICATOR_READ},
                destructive=False,
                owner=OWNER,
                version=VERSION,
            ),
            handler=lambda args, context: compute_short_trend_by_code_tool(
                stock_daily_service=stock_daily_service,
                time=args["time"],
                codes=args["codes"],
                period=args.get("period", 10),
                limit=args.get("limit", 120),
            ),
        )
    )
    registry.register(
        FunctionTool(
            definition=ToolDefinition(
                name="compute_multi_trend",
                description="Load recent bars by stock code and compute Tongdaxin multi-trend baseline.",
                input_schema=_object_schema(
                    {
                        "time": {"type": "string"},
                        "codes": {"type": "array"},
                        "periods": {"type": "array"},
                        "limit": {"type": "integer"},
                    },
                    ["time", "codes"],
                ),
                required_scopes={STOCK_INDICATOR_READ},
                destructive=False,
                owner=OWNER,
                version=VERSION,
            ),
            handler=lambda args, context: compute_multi_trend_by_code_tool(
                stock_daily_service=stock_daily_service,
                time=args["time"],
                codes=args["codes"],
                periods=args.get("periods"),
                limit=args.get("limit", 120),
            ),
        )
    )
    registry.register(
        FunctionTool(
            definition=ToolDefinition(
                name="compute_kdj",
                description="Load recent bars by stock code and compute Tongdaxin KDJ indicator.",
                input_schema=_object_schema(
                    {
                        "time": {"type": "string"},
                        "codes": {"type": "array"},
                        "period": {"type": "integer"},
                        "smooth_k": {"type": "integer"},
                        "smooth_d": {"type": "integer"},
                        "limit": {"type": "integer"},
                    },
                    ["time", "codes"],
                ),
                required_scopes={STOCK_INDICATOR_READ},
                destructive=False,
                owner=OWNER,
                version=VERSION,
            ),
            handler=lambda args, context: compute_kdj_by_code_tool(
                stock_daily_service=stock_daily_service,
                time=args["time"],
                codes=args["codes"],
                period=args.get("period", 9),
                smooth_k=args.get("smooth_k", 3),
                smooth_d=args.get("smooth_d", 3),
                limit=args.get("limit", 120),
            ),
        )
    )
    registry.register(
        FunctionTool(
            definition=ToolDefinition(
                name="compute_amplitude",
                description="Load recent bars by stock code and compute today's B1 amplitude value.",
                input_schema=_object_schema(
                    {
                        "time": {"type": "string"},
                        "codes": {"type": "array"},
                        "limit": {"type": "integer"},
                    },
                    ["time", "codes"],
                ),
                required_scopes={STOCK_INDICATOR_READ},
                destructive=False,
                owner=OWNER,
                version=VERSION,
            ),
            handler=lambda args, context: compute_amplitude_by_code_tool(
                stock_daily_service=stock_daily_service,
                time=args["time"],
                codes=args["codes"],
                limit=args.get("limit", 120),
            ),
        )
    )
    registry.register(
        FunctionTool(
            definition=ToolDefinition(
                name="get_technical_snapshot",
                description="Build technical snapshots for a batch of stock codes from recent daily bars.",
                input_schema=_object_schema(
                    {
                        "symbols": {"type": "array"},
                        "trade_date": {"type": "string"},
                        "lookback_days": {"type": "integer"},
                        "include_bars": {"type": "boolean"},
                    },
                    ["symbols", "trade_date"],
                ),
                required_scopes={STOCK_SNAPSHOT_READ},
                destructive=False,
                owner=OWNER,
                version=VERSION,
                replayable=True,
            ),
            handler=lambda args, context: get_technical_snapshots_tool(stock_daily_service, args),
        )
    )
    registry.register(
        FunctionTool(
            definition=ToolDefinition(
                name="screen_b1_stocks",
                description="Authoritative B1 stock screener: short trend > multi-trend, close > multi-trend, J < 20, amplitude < 7%. Returns stock codes and names that pass all B1 conditions.",
                input_schema=_object_schema({"time": {"type": "string"}}, ["time"]),
                required_scopes={STOCK_SCREENER_READ},
                destructive=False,
                owner=OWNER,
                version=VERSION,
            ),
            handler=lambda args, context: screen_b1_stocks_tool(
                stock_master_service=stock_master_service,
                stock_daily_service=stock_daily_service,
                payload=args,
            ),
        )
    )

    return registry
