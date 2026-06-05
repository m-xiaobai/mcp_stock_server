from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from ..models.db_models import DailyBar
from ..models.response_models import GetStockDailyBarsResponse
from ..services.stock_daily_service import StockDailyService


def _to_float(value: Decimal | int | float) -> float:
    return float(value)


def _safe_null(value: float | None) -> float | None:
    if value is None:
        return None
    if value != value:
        return None
    return value


def _round_snapshot_value(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return round(value, 4)
    if isinstance(value, dict):
        return {key: _round_snapshot_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_round_snapshot_value(item) for item in value]
    return value


def _ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    alpha = 2 / (period + 1)
    result: list[float] = []
    prev: float | None = None
    for value in values:
        prev = value if prev is None else alpha * value + (1 - alpha) * prev
        result.append(prev)
    return result


def _rolling_mean(values: list[float], period: int) -> list[float | None]:
    result: list[float | None] = []
    for index in range(len(values)):
        if index + 1 < period:
            result.append(None)
        else:
            result.append(sum(values[index - period + 1 : index + 1]) / period)
    return result


def _wilder_rsi(values: list[float], period: int) -> list[float | None]:
    if not values:
        return []
    deltas = [0.0]
    for index in range(1, len(values)):
        deltas.append(values[index] - values[index - 1])
    gains = [max(delta, 0.0) for delta in deltas]
    losses = [max(-delta, 0.0) for delta in deltas]
    # Wilder RSI uses smoothed average gain/loss rather than a simple rolling mean.
    avg_gains = _ema(gains, period)
    avg_losses = _ema(losses, period)
    result: list[float | None] = []
    for gain, loss in zip(avg_gains, avg_losses, strict=True):
        if loss == 0:
            result.append(100.0 if gain > 0 else 50.0)
            continue
        rs = gain / loss
        result.append(100 - (100 / (1 + rs)))
    return result


def _macd_signal(dif: float, dea: float, prev_dif: float, prev_dea: float) -> str:
    # Signal classification is derived from the DIF/DEA relationship and whether the pair sits above/below zero.
    curr_diff = dif - dea
    prev_diff = prev_dif - prev_dea
    if prev_diff <= 0 and curr_diff > 0 and dif > 0:
        return "bullish_above_zero"
    if dif > 0 and dea > 0 and curr_diff >= 0:
        return "bullish_above_zero"
    if prev_diff <= 0 and curr_diff > 0:
        return "bullish_recovery"
    if prev_diff >= 0 and curr_diff < 0 and dif > 0:
        return "bearish_above_zero"
    if dif < 0 and dea < 0 and curr_diff <= 0:
        return "bearish_below_zero"
    if prev_diff >= 0 and curr_diff < 0:
        return "bearish_below_zero"
    return "neutral"


def _rsi_state(rsi_12: float | None) -> str:
    if rsi_12 is None:
        return "neutral"
    if rsi_12 > 70:
        return "overbought"
    if rsi_12 >= 60:
        return "strong_not_overbought"
    if rsi_12 >= 40:
        return "neutral"
    if rsi_12 >= 30:
        return "weak"
    return "oversold"


def _volume_price_pattern(latest: DailyBar, prev_close: Decimal, avg_volume_5d: float | None) -> str:
    if avg_volume_5d is None or avg_volume_5d <= 0:
        return "neutral"
    latest_volume = float(latest.vol)
    # Treat >=1.2x the recent average as meaningful expansion; smaller changes stay in "shrink/normal".
    volume_ratio = latest_volume / avg_volume_5d
    volume_up = volume_ratio >= 1.2
    price_up = latest.close > prev_close
    upper_shadow = float(latest.high - max(latest.open, latest.close))
    body = float(abs(latest.close - latest.open))
    total_range = float(latest.high - latest.low)
    weak_close = total_range > 0 and (float(latest.high - latest.close) / total_range) >= 0.45 and volume_up
    if volume_up and price_up:
        return "volume_up_price_up"
    if volume_up and not price_up:
        return "volume_up_price_down"
    if not volume_up and price_up and not weak_close:
        return "volume_shrink_price_up"
    if not volume_up and not price_up:
        return "volume_shrink_price_down"
    if weak_close or (upper_shadow > body and volume_up):
        return "volume_up_weak_close"
    return "neutral"


def _data_sufficiency(
    bars: list[DailyBar],
    high_20d: float | None,
    low_20d: float | None,
    avg_volume_5d: float | None,
) -> str:
    if len(bars) < 20:
        return "insufficient_history"
    if high_20d is not None and low_20d is not None and high_20d == low_20d:
        return "invalid_price_range"
    if avg_volume_5d is None or avg_volume_5d <= 0:
        return "invalid_volume"
    return "ok"


@dataclass(slots=True)
class TechnicalSnapshotService:
    stock_daily_service: StockDailyService

    def _build_snapshot(self, bars: list[DailyBar]) -> dict[str, Any]:
        closes = [_to_float(bar.close) for bar in bars]
        highs = [_to_float(bar.high) for bar in bars]
        lows = [_to_float(bar.low) for bar in bars]
        volumes = [float(bar.vol) for bar in bars]
        latest = bars[-1]
        prev = bars[-2] if len(bars) >= 2 else latest

        # Moving averages are computed on close prices and return None until the window is filled.
        ma5 = _safe_null(_rolling_mean(closes, 5)[-1])
        ma10 = _safe_null(_rolling_mean(closes, 10)[-1])
        ma20 = _safe_null(_rolling_mean(closes, 20)[-1])
        ma60 = _safe_null(_rolling_mean(closes, 60)[-1])

        high_20d = max(highs[-20:]) if len(highs) >= 20 else None
        low_20d = min(lows[-20:]) if len(lows) >= 20 else None
        close = closes[-1]
        prev_close = closes[-2] if len(closes) >= 2 else closes[-1]
        latest_volume = volumes[-1]
        # Follow the plan contract: avg_volume_5d excludes the latest bar, then compares latest against that baseline.
        avg_volume_5d = sum(volumes[-6:-1]) / 5 if len(volumes) >= 6 else None
        volume_ratio = latest_volume / avg_volume_5d if avg_volume_5d else None

        # Standard MACD(12,26,9): DIF = EMA12 - EMA26, DEA = EMA(DIF, 9), BAR = 2 * (DIF - DEA).
        ema12 = _ema(closes, 12)
        ema26 = _ema(closes, 26)
        dif_series = [fast - slow for fast, slow in zip(ema12, ema26, strict=True)]
        dea_series = _ema(dif_series, 9)
        macd_dif = dif_series[-1]
        macd_dea = dea_series[-1]
        macd_bar = (macd_dif - macd_dea) * 2
        prev_dif = dif_series[-2] if len(dif_series) >= 2 else dif_series[-1]
        prev_dea = dea_series[-2] if len(dea_series) >= 2 else dea_series[-1]

        # RSI uses the same close series with three standard lookbacks for short/mid/long momentum states.
        rsi_6_series = _wilder_rsi(closes, 6)
        rsi_12_series = _wilder_rsi(closes, 12)
        rsi_24_series = _wilder_rsi(closes, 24)
        rsi_6 = rsi_6_series[-1]
        rsi_12 = rsi_12_series[-1]
        rsi_24 = rsi_24_series[-1]

        snapshot = {
            "close": close,
            "prev_close": prev_close,
            "high": highs[-1],
            "low": lows[-1],
            "ma5": ma5,
            "ma10": ma10,
            "ma20": ma20,
            "ma60": ma60,
            "high_20d": high_20d,
            "low_20d": low_20d,
            "range_position_20d": ((close - low_20d) / (high_20d - low_20d)) if high_20d is not None and low_20d is not None and high_20d != low_20d else None,
            "close_position": ((close - float(latest.low)) / (float(latest.high) - float(latest.low))) if float(latest.high) != float(latest.low) else None,
            "latest_volume": int(latest.vol),
            "avg_volume_5d": avg_volume_5d,
            "volume_ratio": volume_ratio,
            "close_3d_change_pct": ((close - closes[-4]) / closes[-4] * 100) if len(closes) >= 4 and closes[-4] != 0 else None,
            "macd_dif": macd_dif,
            "macd_dea": macd_dea,
            "macd_bar": macd_bar,
            "macd_signal": _macd_signal(macd_dif, macd_dea, prev_dif, prev_dea),
            "rsi_6": rsi_6,
            "rsi_12": rsi_12,
            "rsi_24": rsi_24,
            "rsi_state": _rsi_state(rsi_12),
            "volume_price_pattern": _volume_price_pattern(latest, Decimal(str(prev_close)), avg_volume_5d),
            "long_upper_shadow": float(latest.high - max(latest.open, latest.close)) / float(latest.high - latest.low) > 0.4 if float(latest.high) != float(latest.low) else False,
            "weak_close_after_intraday_strength": float(latest.high - latest.close) / float(latest.high - latest.low) > 0.45 if float(latest.high) != float(latest.low) else False,
        }
        snapshot["data_sufficiency"] = _data_sufficiency(bars, high_20d, low_20d, avg_volume_5d)
        return _round_snapshot_value(snapshot)

    def get_technical_snapshot(
        self,
        symbol: str,
        trade_date: date,
        lookback_days: int = 60,
        include_bars: bool = False,
    ) -> dict[str, Any]:
        response = self.stock_daily_service.get_stock_daily_bars(
            time=trade_date,
            codes=[symbol],
            limit=lookback_days,
        )
        bars = response.items[0].daily_bars if response.items else []
        if not bars:
            raise ValueError("insufficient_history")
        snapshot = self._build_snapshot(bars)
        payload: dict[str, Any] = {
            "symbol": symbol,
            "trade_date": trade_date.isoformat(),
            "bars_count": len(bars),
            "technical_snapshot": snapshot,
        }
        if include_bars:
            payload["bars"] = [
                {
                    "trade_date": bar.trade_date.isoformat(),
                    "open": str(bar.open),
                    "close": str(bar.close),
                    "high": str(bar.high),
                    "low": str(bar.low),
                    "vol": bar.vol,
                    "amount": str(bar.amount),
                }
                for bar in bars
            ]
        return payload

    def get_technical_snapshots(
        self,
        symbols: list[str],
        trade_date: date,
        lookback_days: int = 60,
        include_bars: bool = False,
    ) -> dict[str, Any]:
        response = self.stock_daily_service.get_stock_daily_bars(
            time=trade_date,
            codes=symbols,
            limit=lookback_days,
        )
        item_map = {item.code: item for item in response.items}
        items: list[dict[str, Any]] = []
        partial_failures: list[dict[str, Any]] = []
        for symbol in symbols:
            item = item_map.get(symbol)
            bars = item.daily_bars if item is not None else []
            if not bars:
                partial_failures.append({"symbol": symbol, "reason": "insufficient_history"})
                continue
            snapshot = self._build_snapshot(bars)
            payload: dict[str, Any] = {
                "symbol": symbol,
                "bars_count": len(bars),
                "technical_snapshot": snapshot,
            }
            if include_bars:
                payload["bars"] = [
                    {
                        "trade_date": bar.trade_date.isoformat(),
                        "open": str(bar.open),
                        "close": str(bar.close),
                        "high": str(bar.high),
                        "low": str(bar.low),
                        "vol": bar.vol,
                        "amount": str(bar.amount),
                    }
                    for bar in bars
                ]
            items.append(payload)
        return {
            "trade_date": trade_date.isoformat(),
            "items": items,
            "partial_failures": partial_failures,
        }
