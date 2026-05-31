from __future__ import annotations

from dataclasses import asdict
from datetime import date
from typing import Any

from ..init.init_stock_daily import fetch_today_stock_daily
from ..init.init_stock_master import fetch_all_stock_codes
from ..models import UpsertStockDailyBarsRequest
from ..services import StockDailyService, StockMasterService


def list_stock_codes_tool(
    stock_master_service: StockMasterService, payload: dict[str, Any]
) -> dict[str, Any]:
    offset = int(payload["offset"])
    limit = int(payload["limit"])
    if offset < 0:
        raise ValueError("offset must be >= 0")
    if limit <= 0:
        raise ValueError("limit must be > 0")

    total_count, rows = stock_master_service.list_stock_codes(offset=offset, limit=limit)
    items = [item.code for item in rows]
    return {
        "total_count": total_count,
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(items) < total_count,
        "items": items,
        "display_notice": "Showing one page only. Data source is complete.",
    }


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


def insert_stock_daily_bars_after_close_tool(
    stock_daily_service: StockDailyService,
    payload: dict[str, Any],
    fetch_codes=fetch_all_stock_codes,
    fetch_rows=fetch_today_stock_daily,
) -> dict[str, Any]:
    codes = [row["code"] for row in fetch_codes()]
    request = UpsertStockDailyBarsRequest.from_dict(
        {
            "time": payload["time"],
            "daily_data": list(fetch_rows(codes)),
        }
    )
    response = stock_daily_service.upsert_stock_daily_bars(request)
    return {
        "time": response.time.isoformat(),
        "total": response.total,
        "success": response.success,
        "failed": response.failed,
        "errors": [asdict(item) for item in response.errors],
    }
