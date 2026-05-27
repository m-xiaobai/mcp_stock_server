from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable

if __package__ in (None, ""):
    import sys

    CURRENT_DIR = Path(__file__).resolve().parent
    PROJECT_ROOT = CURRENT_DIR.parent
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

    from mcp_stock_server.init_stock_master import fetch_all_stock_codes
    from mcp_stock_server.main import build_mysql_services
    from mcp_stock_server.models.request_models import UpsertStockDailyBarsRequest
    from mcp_stock_server.services.stock_daily_service import StockDailyService
else:
    from .init_stock_master import fetch_all_stock_codes
    from .main import build_mysql_services
    from .models.request_models import UpsertStockDailyBarsRequest
    from .services.stock_daily_service import StockDailyService


def fetch_all_stock_daily(codes: list[str]) -> list[dict[str, object]]:
    from mootdx.quotes import Quotes

    client = Quotes.factory(market="std")
    rows: list[dict[str, object]] = []
    for code in codes:
        bars = client.bars(symbol=code, category=4, offset=120)
        if bars is None or getattr(bars, "empty", False):
            continue
        for _, row in bars.iterrows():
            trade_date = str(row["datetime"])[:10]
            rows.append(
                {
                    "code": str(code),
                    "open": row["open"],
                    "close": row["close"],
                    "high": row["high"],
                    "low": row["low"],
                    "vol": int(row["vol"]),
                    "amount": row["amount"],
                    "trade_date": trade_date,
                }
            )
    return rows


def initialize_stock_daily(
    stock_daily_service: StockDailyService,
    codes: list[str],
    fetch_rows: Callable[[list[str]], Iterable[dict[str, object]]] = fetch_all_stock_daily,
) -> int:
    raw_rows = list(fetch_rows(codes))
    if not raw_rows:
        return 0

    grouped: dict[str, list[dict[str, object]]] = {}
    for row in raw_rows:
        row_trade_date = str(row["trade_date"])
        grouped.setdefault(row_trade_date, []).append(row)

    inserted = 0
    for row_trade_date, rows in grouped.items():
        request = UpsertStockDailyBarsRequest.from_dict(
            {
                "time": row_trade_date,
                "daily_data": rows,
            }
        )
        result = stock_daily_service.upsert_stock_daily_bars(request)
        inserted += result.success
    return inserted


def main() -> int:
    _, stock_daily_service = build_mysql_services()
    codes = [row["code"] for row in fetch_all_stock_codes()]
    inserted = initialize_stock_daily(stock_daily_service, codes)
    print(f"stock_daily initialized with {inserted} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
