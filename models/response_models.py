from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .db_models import StockDailyBarsItem


@dataclass(slots=True)
class GetStockDailyBarsResponse:
    time: date
    items: list[StockDailyBarsItem]


@dataclass(slots=True)
class UpsertErrorItem:
    code: str
    reason: str


@dataclass(slots=True)
class UpsertStockDailyBarsResponse:
    time: date
    total: int
    success: int
    failed: int
    errors: list[UpsertErrorItem]

    def __post_init__(self) -> None:
        if self.success + self.failed != self.total:
            raise ValueError("success + failed must equal total")
        if self.failed != len(self.errors):
            raise ValueError("failed must equal len(errors)")
