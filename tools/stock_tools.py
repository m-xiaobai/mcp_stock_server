from __future__ import annotations

from dataclasses import asdict
from datetime import date
from decimal import Decimal
from typing import Any

from ..init.init_stock_daily import fetch_today_stock_daily
from ..init.init_stock_master import fetch_all_stock_codes
from ..models import UpsertStockDailyBarsRequest
from ..services import StockDailyService, StockMasterService
from .indicator_tools import (
    compute_amplitude_by_code_tool,
    compute_kdj_by_code_tool,
    compute_multi_trend_by_code_tool,
    compute_short_trend_by_code_tool,
)


def list_stock_codes_tool(stock_master_service: StockMasterService) -> list[str]:
    return [item.code for item in stock_master_service.list_stock_codes()]


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


def screen_b1_stocks_tool(
    stock_master_service: StockMasterService,
    stock_daily_service: StockDailyService,
    payload: dict[str, Any],
    compute_amplitude_fn=compute_amplitude_by_code_tool,
    compute_kdj_fn=compute_kdj_by_code_tool,
    compute_multi_trend_fn=compute_multi_trend_by_code_tool,
    compute_short_trend_fn=compute_short_trend_by_code_tool,
    get_bars_fn=get_stock_daily_bars_tool,
) -> dict[str, Any]:
    time = payload["time"]
    candidate_codes = list(stock_master_service.list_stock_codes())
    total_candidates = len(candidate_codes)
    if not candidate_codes:
        return {
            "time": time,
            "total_candidates": 0,
            "selected_count": 0,
            "items": [],
        }

    amplitude_response = compute_amplitude_fn(stock_daily_service, time, candidate_codes, limit=120)
    amplitude_map = {item["code"]: item["value"] for item in amplitude_response["items"]}
    remaining_codes = [
        code for code in candidate_codes if amplitude_map.get(code) is not None and amplitude_map[code] < 7
    ]
    if not remaining_codes:
        return {
            "time": time,
            "total_candidates": total_candidates,
            "selected_count": 0,
            "items": [],
        }

    kdj_response = compute_kdj_fn(
        stock_daily_service,
        time,
        remaining_codes,
        period=9,
        smooth_k=3,
        smooth_d=3,
        limit=120,
    )
    j_map = {
        item["code"]: item["j"][-1] if item.get("j") else None
        for item in kdj_response["items"]
    }
    remaining_codes = [
        code for code in remaining_codes if j_map.get(code) is not None and j_map[code] < 20
    ]
    if not remaining_codes:
        return {
            "time": time,
            "total_candidates": total_candidates,
            "selected_count": 0,
            "items": [],
        }

    multi_trend_response = compute_multi_trend_fn(
        stock_daily_service,
        time,
        remaining_codes,
        periods=[14, 28, 57, 114],
        limit=120,
    )
    multi_trend_map = {
        item["code"]: item["values"][-1] if item.get("values") else None
        for item in multi_trend_response["items"]
    }

    short_trend_response = compute_short_trend_fn(
        stock_daily_service,
        time,
        remaining_codes,
        period=10,
        limit=120,
    )
    short_trend_map = {
        item["code"]: item["values"][-1] if item.get("values") else None
        for item in short_trend_response["items"]
    }
    remaining_codes = [
        code
        for code in remaining_codes
        if multi_trend_map.get(code) is not None
        and short_trend_map.get(code) is not None
        and short_trend_map[code] > multi_trend_map[code]
    ]
    if not remaining_codes:
        return {
            "time": time,
            "total_candidates": total_candidates,
            "selected_count": 0,
            "items": [],
        }

    bars_response = get_bars_fn(stock_daily_service, {"time": time, "codes": remaining_codes})
    close_map = {
        item["code"]: float(Decimal(item["daily_bars"][-1]["close"])) if item.get("daily_bars") else None
        for item in bars_response["items"]
    }

    selected_items = []
    for code in remaining_codes:
        multi_trend = multi_trend_map.get(code)
        close_value = close_map.get(code)
        if multi_trend is None or close_value is None or close_value <= multi_trend:
            continue
        selected_items.append(
            {
                "code": code,
                "amplitude": amplitude_map[code],
                "j": j_map[code],
                "multi_trend": multi_trend,
                "short_trend": short_trend_map[code],
                "close": close_value,
            }
        )

    return {
        "time": time,
        "total_candidates": total_candidates,
        "selected_count": len(selected_items),
        "items": selected_items,
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
