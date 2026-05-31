from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from ..models import StockCodeItem
from ..repositories import StockMasterRepository


@dataclass(slots=True)
class StockMasterService:
    stock_master_repository: StockMasterRepository

    def list_stock_codes(self, offset: int, limit: int) -> tuple[int, list[StockCodeItem]]:
        return self.stock_master_repository.list_page(offset=offset, limit=limit)

    def initialize_stock_master(self, rows: Iterable[Mapping[str, object]]) -> int:
        normalized = [
            StockCodeItem(code=str(row["code"]).strip(), name=str(row["name"]).strip())
            for row in rows
            if str(row.get("code", "")).strip() and str(row.get("name", "")).strip()
        ]
        if not normalized:
            return 0
        return self.stock_master_repository.batch_insert(normalized)
