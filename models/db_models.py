from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(slots=True)
class StockCodeItem:
    code: str
    name: str


@dataclass(slots=True)
class DailyBar:
    code: str
    trade_date: date
    open: Decimal
    close: Decimal
    high: Decimal
    low: Decimal
    vol: int
    amount: Decimal

    def __post_init__(self) -> None:
        if self.vol < 0:
            raise ValueError("vol must be >= 0")
        if self.amount < 0:
            raise ValueError("amount must be >= 0")
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("high must be >= open, close, low")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("low must be <= open, close, high")


@dataclass(slots=True)
class StockDailyBarsItem:
    code: str
    daily_bars: list[DailyBar]
