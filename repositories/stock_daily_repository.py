from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Callable, Protocol

from ..models import DailyBar, UpsertStockDailyDataItem

ConnectionFactory = Callable[[], Any]


class StockDailyRepository(Protocol):
    def get_recent_bars_by_codes(
        self, as_of: date, codes: list[str], limit: int
    ) -> list[DailyBar]:
        ...

    def existing_daily_keys(self, keys: list[tuple[str, date]]) -> set[tuple[str, date]]:
        ...

    def batch_insert(self, rows: list[UpsertStockDailyDataItem]) -> int:
        ...

    def batch_update(self, rows: list[UpsertStockDailyDataItem]) -> int:
        ...

    def batch_upsert(self, rows: list[UpsertStockDailyDataItem]) -> int:
        ...


@dataclass(slots=True)
class InMemoryStockDailyRepository:
    items: list[DailyBar]

    def get_recent_bars_by_codes(
        self, as_of: date, codes: list[str], limit: int
    ) -> list[DailyBar]:
        grouped: list[DailyBar] = []
        for code in codes:
            rows = [
                item
                for item in self.items
                if item.code == code and item.trade_date <= as_of
            ]
            rows.sort(key=lambda item: item.trade_date, reverse=True)
            recent_rows = rows[:limit]
            recent_rows.sort(key=lambda item: item.trade_date)
            grouped.extend(recent_rows)
        return grouped

    def existing_daily_keys(self, keys: list[tuple[str, date]]) -> set[tuple[str, date]]:
        existing = {(item.code, item.trade_date) for item in self.items}
        return {key for key in keys if key in existing}

    def batch_insert(self, rows: list[UpsertStockDailyDataItem]) -> int:
        return len(rows)

    def batch_update(self, rows: list[UpsertStockDailyDataItem]) -> int:
        return len(rows)

    def batch_upsert(self, rows: list[UpsertStockDailyDataItem]) -> int:
        index_by_key = {(item.code, item.trade_date): idx for idx, item in enumerate(self.items)}
        for row in rows:
            key = (row.code, row.trade_date)
            daily_bar = DailyBar(
                code=row.code,
                trade_date=row.trade_date,
                open=row.open,
                close=row.close,
                high=row.high,
                low=row.low,
                vol=row.vol,
                amount=row.amount,
            )
            if key in index_by_key:
                self.items[index_by_key[key]] = daily_bar
            else:
                index_by_key[key] = len(self.items)
                self.items.append(daily_bar)
        return len(rows)


@dataclass(slots=True)
class MySQLStockDailyRepository:
    connection_factory: ConnectionFactory

    def get_recent_bars_by_codes(
        self, as_of: date, codes: list[str], limit: int
    ) -> list[DailyBar]:
        if not codes:
            return []
        placeholders = ", ".join(["%s"] * len(codes))
        sql = f"""
SELECT *
FROM (
  SELECT
    stock_code,
    open,
    close,
    high,
    low,
    vol,
    amount,
    trade_date,
    ROW_NUMBER() OVER (
      PARTITION BY stock_code
      ORDER BY trade_date DESC
    ) AS rn
  FROM stock_daily
  WHERE stock_code IN ({placeholders})
    AND trade_date <= %s
) t
WHERE t.rn <= %s
ORDER BY stock_code, trade_date ASC
""".strip()
        params = tuple(codes) + (as_of, limit)
        connection = self.connection_factory()
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
        return [
            DailyBar(
                code=row["stock_code"],
                trade_date=row["trade_date"],
                open=Decimal(str(row["open"])),
                close=Decimal(str(row["close"])),
                high=Decimal(str(row["high"])),
                low=Decimal(str(row["low"])),
                vol=int(row["vol"]),
                amount=Decimal(str(row["amount"])),
            )
            for row in rows
        ]

    def existing_daily_keys(self, keys: list[tuple[str, date]]) -> set[tuple[str, date]]:
        if not keys:
            return set()
        conditions = " OR ".join(["(stock_code = %s AND trade_date = %s)"] * len(keys))
        sql = f"""
SELECT stock_code, trade_date
FROM stock_daily
WHERE {conditions}
""".strip()
        params: list[Any] = []
        for code, trade_date in keys:
            params.extend([code, trade_date])
        connection = self.connection_factory()
        with connection.cursor() as cursor:
            cursor.execute(sql, tuple(params))
            rows = cursor.fetchall()
        return {(row["stock_code"], row["trade_date"]) for row in rows}

    def batch_insert(self, rows: list[UpsertStockDailyDataItem]) -> int:
        if not rows:
            return 0
        sql = """
INSERT INTO stock_daily (
    stock_code,
    open,
    close,
    high,
    low,
    vol,
    amount,
    trade_date
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
""".strip()
        params = [
            (
                row.code,
                row.open,
                row.close,
                row.high,
                row.low,
                row.vol,
                row.amount,
                row.trade_date,
            )
            for row in rows
        ]
        connection = self.connection_factory()
        with connection.cursor() as cursor:
            cursor.executemany(sql, params)
        if hasattr(connection, "commit"):
            connection.commit()
        return len(rows)

    def batch_update(self, rows: list[UpsertStockDailyDataItem]) -> int:
        if not rows:
            return 0
        sql = """
UPDATE stock_daily
SET
    open = %s,
    close = %s,
    high = %s,
    low = %s,
    vol = %s,
    amount = %s,
    update_time = CURRENT_TIMESTAMP
WHERE stock_code = %s
  AND trade_date = %s
""".strip()
        params = [
            (
                row.open,
                row.close,
                row.high,
                row.low,
                row.vol,
                row.amount,
                row.code,
                row.trade_date,
            )
            for row in rows
        ]
        connection = self.connection_factory()
        with connection.cursor() as cursor:
            cursor.executemany(sql, params)
        if hasattr(connection, "commit"):
            connection.commit()
        return len(rows)

    def batch_upsert(self, rows: list[UpsertStockDailyDataItem]) -> int:
        if not rows:
            return 0
        sql = """
INSERT INTO stock_daily (
    stock_code,
    open,
    close,
    high,
    low,
    vol,
    amount,
    trade_date
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
ON DUPLICATE KEY UPDATE
    open = VALUES(open),
    close = VALUES(close),
    high = VALUES(high),
    low = VALUES(low),
    vol = VALUES(vol),
    amount = VALUES(amount),
    update_time = CURRENT_TIMESTAMP
""".strip()
        params = [
            (
                row.code,
                row.open,
                row.close,
                row.high,
                row.low,
                row.vol,
                row.amount,
                row.trade_date,
            )
            for row in rows
        ]
        connection = self.connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.executemany(sql, params)
            if hasattr(connection, "commit"):
                connection.commit()
        except Exception:
            if hasattr(connection, "rollback"):
                connection.rollback()
            raise
        return len(rows)
