from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any


def _parse_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def _parse_decimal(value: Any) -> Decimal:
    return Decimal(str(value))


@dataclass(slots=True)
class UpsertStockDailyDataItem:
    code: str
    open: Decimal
    close: Decimal
    high: Decimal
    low: Decimal
    vol: int
    amount: Decimal
    trade_date: date

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
class UpsertStockDailyBarsRequest:
    time: date
    daily_data: list[UpsertStockDailyDataItem]

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "UpsertStockDailyBarsRequest":
        items = [
            UpsertStockDailyDataItem(
                code=str(item["code"]).strip(),
                open=_parse_decimal(item["open"]),
                close=_parse_decimal(item["close"]),
                high=_parse_decimal(item["high"]),
                low=_parse_decimal(item["low"]),
                vol=int(item["vol"]),
                amount=_parse_decimal(item["amount"]),
                trade_date=_parse_date(item["trade_date"]),
            )
            for item in payload["daily_data"]
        ]
        parsed = cls(time=_parse_date(payload["time"]), daily_data=items)
        for item in parsed.daily_data:
            if item.trade_date != parsed.time:
                raise ValueError(
                    f"trade_date {item.trade_date} does not match request time {parsed.time}"
                )
        return parsed
