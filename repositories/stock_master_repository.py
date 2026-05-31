from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol

from ..models import StockCodeItem

ConnectionFactory = Callable[[], Any]


class StockMasterRepository(Protocol):
    def list_page(self, offset: int, limit: int) -> tuple[int, list[StockCodeItem]]:
        ...

    def existing_codes(self, codes: list[str]) -> set[str]:
        ...

    def batch_insert(self, rows: list[StockCodeItem]) -> int:
        ...


@dataclass(slots=True)
class InMemoryStockMasterRepository:
    items: list[StockCodeItem]

    def list_page(self, offset: int, limit: int) -> tuple[int, list[StockCodeItem]]:
        sorted_items = sorted(self.items, key=lambda item: item.code)
        return len(sorted_items), sorted_items[offset : offset + limit]

    def existing_codes(self, codes: list[str]) -> set[str]:
        known = {item.code for item in self.items}
        return {code for code in codes if code in known}

    def batch_insert(self, rows: list[StockCodeItem]) -> int:
        self.items.extend(rows)
        return len(rows)


@dataclass(slots=True)
class MySQLStockMasterRepository:
    connection_factory: ConnectionFactory

    def list_page(self, offset: int, limit: int) -> tuple[int, list[StockCodeItem]]:
        count_sql = """
SELECT COUNT(*) AS total_count
FROM stock_master
""".strip()
        page_sql = """
SELECT code, name
FROM stock_master
ORDER BY code ASC
LIMIT %s OFFSET %s
""".strip()
        connection = self.connection_factory()
        with connection.cursor() as cursor:
            cursor.execute(count_sql)
            total_count_row = cursor.fetchall()[0]
        with connection.cursor() as cursor:
            cursor.execute(page_sql, (limit, offset))
            rows = cursor.fetchall()
        return (
            int(total_count_row["total_count"]),
            [StockCodeItem(code=row["code"], name=row["name"]) for row in rows],
        )

    def existing_codes(self, codes: list[str]) -> set[str]:
        if not codes:
            return set()
        placeholders = ", ".join(["%s"] * len(codes))
        sql = f"""
SELECT code
FROM stock_master
WHERE code IN ({placeholders})
""".strip()
        connection = self.connection_factory()
        with connection.cursor() as cursor:
            cursor.execute(sql, tuple(codes))
            rows = cursor.fetchall()
        return {row["code"] for row in rows}

    def batch_insert(self, rows: list[StockCodeItem]) -> int:
        if not rows:
            return 0
        sql = """
INSERT INTO stock_master (
    code,
    name
) VALUES (%s, %s)
""".strip()
        params = [(row.code, row.name) for row in rows]
        connection = self.connection_factory()
        with connection.cursor() as cursor:
            cursor.executemany(sql, params)
        if hasattr(connection, "commit"):
            connection.commit()
        return len(rows)
