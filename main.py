from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from dataclasses import dataclass
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


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class MCPRuntimeConfig:
    transport: str = "stdio"
    host: str = "127.0.0.1"
    port: int = 8000
    streamable_http_path: str = "/mcp"

    @classmethod
    def from_file(cls, path: str | Path) -> "MCPRuntimeConfig":
        config_path = Path(path)
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        mcp_payload = payload.get("mcp", {})
        return cls(
            transport=str(mcp_payload.get("transport", "stdio")),
            host=str(mcp_payload.get("host", "127.0.0.1")),
            port=int(mcp_payload.get("port", 8000)),
            streamable_http_path=str(mcp_payload.get("streamable_http_path", "/mcp")),
        )


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
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    if __package__ in (None, ""):
        from mcp_stock_server.server import run_stdio_server, run_streamable_http_server
    else:
        from .server import run_stdio_server, run_streamable_http_server
    logger.info("Starting MCP Stock Server")
    stock_master_service, stock_daily_service = build_mysql_services()
    runtime_config = MCPRuntimeConfig.from_file(Path(__file__).with_name("config.json"))
    transport = runtime_config.transport
    if len(sys.argv) > 1:
        transport = sys.argv[1]
    if transport == "streamable-http":
        run_streamable_http_server(
            stock_master_service,
            stock_daily_service,
            host=runtime_config.host,
            port=runtime_config.port,
            streamable_http_path=runtime_config.streamable_http_path,
        )
    else:
        run_stdio_server(stock_master_service, stock_daily_service)
    logger.info("MCP Stock Server running")
