from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import DefaultDict

from ..models.request_models import UpsertStockDailyBarsRequest, UpsertStockDailyDataItem
from ..models.db_models import DailyBar, StockDailyBarsItem
from ..models.response_models import (
    GetStockDailyBarsResponse,
    UpsertErrorItem,
    UpsertStockDailyBarsResponse,
)
from ..repositories.stock_daily_repository import StockDailyRepository
from ..repositories.stock_master_repository import StockMasterRepository


@dataclass(slots=True)
class StockDailyService:
    stock_master_repository: StockMasterRepository
    stock_daily_repository: StockDailyRepository

    def get_stock_daily_bars(
        self, time: date, codes: list[str], limit: int = 120
    ) -> GetStockDailyBarsResponse:
        rows: list[DailyBar] = self.stock_daily_repository.get_recent_bars_by_codes(
            time, codes, limit
        )
        grouped: DefaultDict[str, list[DailyBar]] = defaultdict(list)
        for row in rows:
            grouped[row.code].append(row)
        items = [StockDailyBarsItem(code=code, daily_bars=grouped[code]) for code in codes]
        return GetStockDailyBarsResponse(time=time, items=items)

    def upsert_stock_daily_bars(
        self, request: UpsertStockDailyBarsRequest
    ) -> UpsertStockDailyBarsResponse:
        existing_codes: set[str] = self.stock_master_repository.existing_codes(
            [item.code for item in request.daily_data]
        )
        valid_rows: list[UpsertStockDailyDataItem] = []
        errors: list[UpsertErrorItem] = []
        for item in request.daily_data:
            if item.code not in existing_codes:
                errors.append(UpsertErrorItem(code=item.code, reason="stock code not found"))
                continue
            valid_rows.append(item)

        success = 0
        if valid_rows:
            success += self.stock_daily_repository.batch_upsert(valid_rows)

        failed = len(errors)
        return UpsertStockDailyBarsResponse(
            time=request.time,
            total=len(request.daily_data),
            success=success,
            failed=failed,
            errors=errors,
        )
