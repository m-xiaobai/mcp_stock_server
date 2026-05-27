from __future__ import annotations

from pathlib import Path
from typing import Callable

if __package__ in (None, ""):
    import sys

    CURRENT_DIR = Path(__file__).resolve().parent
    PROJECT_ROOT = CURRENT_DIR.parent
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

    from mcp_stock_server.db import MySQLConfig, create_pymysql_connection
    from mcp_stock_server.repositories.stock_daily_repository import (
        InMemoryStockDailyRepository,
        MySQLStockDailyRepository,
    )
    from mcp_stock_server.repositories.stock_master_repository import (
        InMemoryStockMasterRepository,
        MySQLStockMasterRepository,
    )
    from mcp_stock_server.services import StockDailyService, StockMasterService
else:
    from .db import MySQLConfig, create_pymysql_connection
    from .repositories.stock_daily_repository import (
        InMemoryStockDailyRepository,
        MySQLStockDailyRepository,
    )
    from .repositories.stock_master_repository import (
        InMemoryStockMasterRepository,
        MySQLStockMasterRepository,
    )
    from .services import StockDailyService, StockMasterService


def build_demo_services() -> tuple[StockMasterService, StockDailyService]:
    master_repo = InMemoryStockMasterRepository(items=[])
    daily_repo = InMemoryStockDailyRepository(items=[])
    return (
        StockMasterService(master_repo),
        StockDailyService(master_repo, daily_repo),
    )


def build_mysql_services(
    config: MySQLConfig | None = None,
    config_path: str | Path | None = None,
    connection_factory_builder: Callable[[MySQLConfig], Callable[[], object]] | None = None,
) -> tuple[StockMasterService, StockDailyService]:
    if config is not None:
        resolved_config = config
    elif config_path is not None:
        resolved_config = MySQLConfig.from_file(config_path)
    else:
        resolved_config = MySQLConfig.from_file(Path(__file__).with_name("config.json"))

    if connection_factory_builder is None:

        def connection_factory_builder(current_config: MySQLConfig) -> Callable[[], object]:
            return lambda: create_pymysql_connection(current_config)

    connection_factory = connection_factory_builder(resolved_config)
    master_repo = MySQLStockMasterRepository(connection_factory)
    daily_repo = MySQLStockDailyRepository(connection_factory)
    return (
        StockMasterService(master_repo),
        StockDailyService(master_repo, daily_repo),
    )


if __name__ == "__main__":
    if __package__ in (None, ""):
        from mcp_stock_server.server import run_stdio_server
    else:
        from .server import run_stdio_server
    print("Starting MCP Stock Server...")
    stock_master_service, stock_daily_service = build_mysql_services()
    run_stdio_server(stock_master_service, stock_daily_service)
    print("MCP Stock Server running...")
