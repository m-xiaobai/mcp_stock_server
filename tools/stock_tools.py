from __future__ import annotations

from dataclasses import asdict
from datetime import date
from typing import Any

from ..models import UpsertStockDailyBarsRequest
from ..services import StockDailyService, StockMasterService


def list_stock_codes_tool(stock_master_service: StockMasterService) -> dict[str, Any]:
    return {"items": [asdict(item) for item in stock_master_service.list_stock_codes()]}


def get_stock_daily_bars_tool(
    stock_daily_service: StockDailyService, payload: dict[str, Any]
) -> dict[str, Any]:
    time = date.fromisoformat(payload["time"])
    response = stock_daily_service.get_stock_daily_bars(time=time, codes=payload["codes"])
    return {
        "time": response.time.isoformat(),
        "items": [
            {
                "code": item.code,
                "daily_bars": [
                    {
                        "trade_date": bar.trade_date.isoformat(),
                        "open": str(bar.open),
                        "close": str(bar.close),
                        "high": str(bar.high),
                        "low": str(bar.low),
                        "vol": bar.vol,
                        "amount": str(bar.amount),
                    }
                    for bar in item.daily_bars
                ],
            }
            for item in response.items
        ],
    }


def upsert_stock_daily_bars_tool(
    stock_daily_service: StockDailyService, payload: dict[str, Any]
) -> dict[str, Any]:
    request = UpsertStockDailyBarsRequest.from_dict(payload)
    response = stock_daily_service.upsert_stock_daily_bars(request)
    return {
        "time": response.time.isoformat(),
        "total": response.total,
        "success": response.success,
        "failed": response.failed,
        "errors": [asdict(item) for item in response.errors],
    }
