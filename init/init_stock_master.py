from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable

if __package__ in (None, ""):
    import sys

    CURRENT_DIR = Path(__file__).resolve().parent
    PROJECT_ROOT = CURRENT_DIR.parent
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

    from mcp_stock_server.main import build_mysql_services
    from mcp_stock_server.services.stock_master_service import StockMasterService
else:
    from ..main import build_mysql_services
    from ..services.stock_master_service import StockMasterService


def fetch_all_stock_codes() -> list[dict[str, str]]:
    import pandas as pd
    from mootdx import consts
    from mootdx.quotes import Quotes

    client = Quotes.factory(market="std")
    sh_symbol = client.stocks(market=consts.MARKET_SH)
    sz_symbol = client.stocks(market=consts.MARKET_SZ)

    sh_filtered = sh_symbol[
        sh_symbol["code"].astype(str).str.startswith(("600", "601", "603", "605", "688"))
    ]
    sz_filtered = sz_symbol[
        sz_symbol["code"].astype(str).str.startswith(("000", "001", "002", "003", "300"))
    ]
    filtered = pd.concat([sh_filtered, sz_filtered], ignore_index=True)[["code", "name"]]
    filtered["code"] = filtered["code"].astype(str).str.strip()
    filtered["name"] = filtered["name"].astype(str).str.strip()
    return filtered.to_dict(orient="records")


def initialize_stock_master_from_source(stock_master_service: StockMasterService) -> int:
    rows: Iterable[dict[str, str]] = fetch_all_stock_codes()
    return stock_master_service.initialize_stock_master(rows)


def initialize_if_empty(
    stock_master_service: StockMasterService,
    fetch_rows: Callable[[], Iterable[dict[str, str]]] = fetch_all_stock_codes,
) -> int:
    existing = stock_master_service.list_stock_codes()
    if existing:
        return 0
    rows = fetch_rows()
    return stock_master_service.initialize_stock_master(rows)


def main() -> int:
    stock_master_service, _ = build_mysql_services()
    inserted = initialize_if_empty(stock_master_service)
    if inserted == 0:
        print("stock_master is not empty, skip initialization")
    else:
        print(f"stock_master initialized with {inserted} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
