from __future__ import annotations

import math
from collections import deque
from datetime import date
from decimal import Decimal
from typing import Any

from ..services import StockDailyService


B1_PERIODS = (14, 28, 57, 114)


def ema(values: list[float], period: int) -> list[float]:
    """计算标准指数移动平均 EMA。"""
    if period <= 0:
        raise ValueError("period must be positive")
    if not values:
        return []

    alpha = 2 / (period + 1)
    result: list[float] = []
    prev: float | None = None
    for value in values:
        if prev is None:
            prev = value
        else:
            prev = alpha * value + (1 - alpha) * prev
        result.append(prev)
    return result


def sma_cn(values: list[float | None], n: int, m: int) -> list[float | None]:
    """计算通达信风格的 SMA。"""
    if n <= 0:
        raise ValueError("n must be positive")
    if m <= 0:
        raise ValueError("m must be positive")

    result: list[float | None] = []
    prev: float | None = None
    for value in values:
        if value is None:
            result.append(None)
            continue
        if prev is None:
            prev = value
        else:
            prev = (m * value + (n - m) * prev) / n
        result.append(prev)
    return result


def rolling_mean(values: list[float], period: int) -> list[float | None]:
    """计算固定窗口均值，在窗口未满前返回 None。"""
    if period <= 0:
        raise ValueError("period must be positive")

    result: list[float | None] = []
    window: deque[float] = deque()
    window_sum = 0.0
    for value in values:
        window.append(value)
        window_sum += value
        if len(window) > period:
            window_sum -= window.popleft()
        if len(window) == period:
            result.append(window_sum / period)
        else:
            result.append(None)
    return result


def rolling_min(values: list[float], period: int) -> list[float | None]:
    """计算固定窗口最小值，在数据不足前返回 None。"""
    if period <= 0:
        raise ValueError("period must be positive")

    result: list[float | None] = []
    for index in range(len(values)):
        if index + 1 < period:
            result.append(None)
        else:
            result.append(min(values[index - period + 1 : index + 1]))
    return result


def rolling_max(values: list[float], period: int) -> list[float | None]:
    """计算固定窗口最大值，在数据不足前返回 None。"""
    if period <= 0:
        raise ValueError("period must be positive")

    result: list[float | None] = []
    for index in range(len(values)):
        if index + 1 < period:
            result.append(None)
        else:
            result.append(max(values[index - period + 1 : index + 1]))
    return result


def compute_short_trend(closes: list[float], period: int = 10) -> list[float]:
    """计算通达信短期趋势线：EMA(EMA(C, period), period)。"""
    ema_once = ema(closes, period)
    return ema(ema_once, period)


def compute_multi_trend(
    closes: list[float],
    periods: tuple[int, ...] = B1_PERIODS,
) -> list[float | None]:
    """计算多空线基准：多个均线的等权平均值。"""
    ma_lists = [rolling_mean(closes, period) for period in periods]
    result: list[float | None] = []
    for index in range(len(closes)):
        values = [series[index] for series in ma_lists]
        if any(value is None for value in values):
            result.append(None)
        else:
            result.append(sum(values) / len(values))  # type: ignore[arg-type]
    return result


def compute_kdj(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = 9,
    smooth_k: int = 3,
    smooth_d: int = 3,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """按通达信常用参数计算 KDJ 指标。"""
    if not (len(highs) == len(lows) == len(closes)):
        raise ValueError("highs, lows, and closes must have the same length")

    llv = rolling_min(lows, period)
    hhv = rolling_max(highs, period)

    rsv: list[float | None] = []
    for index in range(len(closes)):
        low_value = llv[index]
        high_value = hhv[index]
        if low_value is None or high_value is None or math.isclose(high_value, low_value):
            rsv.append(None)
        else:
            rsv.append((closes[index] - low_value) / (high_value - low_value) * 100)

    k = sma_cn(rsv, smooth_k, 1)
    d = sma_cn(k, smooth_d, 1)
    j: list[float | None] = []
    for index in range(len(closes)):
        if k[index] is None or d[index] is None:
            j.append(None)
        else:
            j.append(3 * k[index] - 2 * d[index])  # type: ignore[operator]
    return k, d, j


def compute_amplitude(
    highs: list[float],
    lows: list[float],
    closes: list[float],
) -> list[float | None]:
    """按 B1 公式计算振幅：(high - low) / REF(close, 1) * 100。"""
    if not (len(highs) == len(lows) == len(closes)):
        raise ValueError("highs, lows, and closes must have the same length")

    result: list[float | None] = [None]
    for index in range(1, len(closes)):
        prev_close = closes[index - 1]
        if math.isclose(prev_close, 0.0):
            result.append(None)
        else:
            result.append((highs[index] - lows[index]) / prev_close * 100)
    return result


def compute_short_trend_tool(closes: list[float], period: int = 10) -> dict[str, Any]:
    return {"values": compute_short_trend(closes=closes, period=period)}


def compute_multi_trend_tool(
    closes: list[float],
    periods: list[int] | None = None,
) -> dict[str, Any]:
    normalized_periods = tuple(periods) if periods is not None else B1_PERIODS
    return {"values": compute_multi_trend(closes=closes, periods=normalized_periods)}


def compute_kdj_tool(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = 9,
    smooth_k: int = 3,
    smooth_d: int = 3,
) -> dict[str, Any]:
    k, d, j = compute_kdj(
        highs=highs,
        lows=lows,
        closes=closes,
        period=period,
        smooth_k=smooth_k,
        smooth_d=smooth_d,
    )
    return {"k": k, "d": d, "j": j}


def compute_amplitude_tool(
    highs: list[float],
    lows: list[float],
    closes: list[float],
) -> dict[str, Any]:
    return {"values": compute_amplitude(highs=highs, lows=lows, closes=closes)}


def _load_price_series(
    stock_daily_service: StockDailyService,
    time: str,
    code: str,
    limit: int,
) -> tuple[date, list[float], list[float], list[float]]:
    as_of = date.fromisoformat(time)
    response = stock_daily_service.get_stock_daily_bars(time=as_of, codes=[code], limit=limit)
    if not response.items or not response.items[0].daily_bars:
        raise ValueError(f"no daily bars found for code {code}")

    bars = response.items[0].daily_bars
    highs = [float(Decimal(bar.high)) for bar in bars]
    lows = [float(Decimal(bar.low)) for bar in bars]
    closes = [float(Decimal(bar.close)) for bar in bars]
    return as_of, highs, lows, closes


def _load_price_series_by_codes(
    stock_daily_service: StockDailyService,
    time: str,
    codes: list[str],
    limit: int,
) -> tuple[date, list[tuple[str, list[float], list[float], list[float]]]]:
    as_of = date.fromisoformat(time)
    response = stock_daily_service.get_stock_daily_bars(time=as_of, codes=codes, limit=limit)
    item_map = {item.code: item for item in response.items}

    results: list[tuple[str, list[float], list[float], list[float]]] = []
    for code in codes:
        item = item_map.get(code)
        if item is None or not item.daily_bars:
            raise ValueError(f"no daily bars found for code {code}")

        highs = [float(Decimal(bar.high)) for bar in item.daily_bars]
        lows = [float(Decimal(bar.low)) for bar in item.daily_bars]
        closes = [float(Decimal(bar.close)) for bar in item.daily_bars]
        results.append((code, highs, lows, closes))
    return as_of, results


def compute_short_trend_by_code_tool(
    stock_daily_service: StockDailyService,
    time: str,
    code: str,
    period: int = 10,
    limit: int = 120,
) -> dict[str, Any]:
    as_of, _, _, closes = _load_price_series(stock_daily_service, time, code, limit)
    return {
        "time": as_of.isoformat(),
        "code": code,
        "values": compute_short_trend(closes=closes, period=period),
    }


def compute_multi_trend_by_code_tool(
    stock_daily_service: StockDailyService,
    time: str,
    code: str,
    periods: list[int] | None = None,
    limit: int = 120,
) -> dict[str, Any]:
    as_of, _, _, closes = _load_price_series(stock_daily_service, time, code, limit)
    normalized_periods = tuple(periods) if periods is not None else B1_PERIODS
    return {
        "time": as_of.isoformat(),
        "code": code,
        "values": compute_multi_trend(closes=closes, periods=normalized_periods),
    }


def compute_kdj_by_code_tool(
    stock_daily_service: StockDailyService,
    time: str,
    codes: list[str],
    period: int = 9,
    smooth_k: int = 3,
    smooth_d: int = 3,
    limit: int = 120,
) -> dict[str, Any]:
    as_of, price_series_items = _load_price_series_by_codes(stock_daily_service, time, codes, limit)
    items = []
    for code, highs, lows, closes in price_series_items:
        k, d, j = compute_kdj(
            highs=highs,
            lows=lows,
            closes=closes,
            period=period,
            smooth_k=smooth_k,
            smooth_d=smooth_d,
        )
        items.append({"code": code, "k": k, "d": d, "j": j})
    return {
        "time": as_of.isoformat(),
        "items": items,
    }


def compute_amplitude_by_code_tool(
    stock_daily_service: StockDailyService,
    time: str,
    codes: list[str],
    limit: int = 120,
) -> dict[str, Any]:
    as_of, price_series_items = _load_price_series_by_codes(stock_daily_service, time, codes, limit)
    return {
        "time": as_of.isoformat(),
        "items": [
            {
                "code": code,
                "value": compute_amplitude(highs=highs, lows=lows, closes=closes)[-1],
            }
            for code, highs, lows, closes in price_series_items
        ],
    }
