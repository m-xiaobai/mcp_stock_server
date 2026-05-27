import unittest
import importlib.util
from datetime import date
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory


class MCPStockServerTests(unittest.TestCase):
    def test_upsert_request_rejects_mismatched_trade_date(self):
        from mcp_stock_server.models.request_models import UpsertStockDailyBarsRequest

        with self.assertRaises(ValueError):
            UpsertStockDailyBarsRequest.from_dict(
                {
                    "time": "2026-05-26",
                    "daily_data": [
                        {
                            "code": "000001",
                            "open": "12.31",
                            "close": "12.58",
                            "high": "12.66",
                            "low": "12.20",
                            "vol": 1532456,
                            "amount": "1923456789",
                            "trade_date": "2026-05-25",
                        }
                    ],
                }
            )

    def test_query_service_groups_rows_by_stock_code(self):
        from mcp_stock_server.models.db_models import DailyBar
        from mcp_stock_server.services.stock_daily_service import StockDailyService

        class FakeDailyRepository:
            def get_recent_bars_by_codes(self, as_of, codes, limit):
                self.last_call = (as_of, codes, limit)
                return [
                    DailyBar(
                        code="000001",
                        trade_date=date(2026, 5, 26),
                        open=Decimal("12.31"),
                        close=Decimal("12.58"),
                        high=Decimal("12.66"),
                        low=Decimal("12.20"),
                        vol=1532456,
                        amount=Decimal("1923456789"),
                    ),
                    DailyBar(
                        code="000002",
                        trade_date=date(2026, 5, 26),
                        open=Decimal("8.11"),
                        close=Decimal("8.25"),
                        high=Decimal("8.30"),
                        low=Decimal("8.05"),
                        vol=2234567,
                        amount=Decimal("1432456789"),
                    ),
                ]

        class FakeMasterRepository:
            def list_all(self):
                return []

        service = StockDailyService(
            stock_master_repository=FakeMasterRepository(),
            stock_daily_repository=FakeDailyRepository(),
        )

        response = service.get_stock_daily_bars(
            time=date(2026, 5, 26),
            codes=["000001", "000002"],
        )

        self.assertEqual(response.time, date(2026, 5, 26))
        self.assertEqual(len(response.items), 2)
        self.assertEqual(response.items[0].code, "000001")
        self.assertEqual(response.items[1].code, "000002")
        self.assertEqual(response.items[0].daily_bars[0].close, Decimal("12.58"))

    def test_list_stock_codes_tool_returns_serialized_items(self):
        from mcp_stock_server.models.db_models import StockCodeItem
        from mcp_stock_server.tools.stock_tools import list_stock_codes_tool

        class FakeStockMasterService:
            def list_stock_codes(self):
                return [
                    StockCodeItem(code="600000", name="浦发银行"),
                    StockCodeItem(code="000001", name="平安银行"),
                ]

        payload = list_stock_codes_tool(FakeStockMasterService())

        self.assertEqual(
            payload,
            {
                "items": [
                    {"code": "600000", "name": "浦发银行"},
                    {"code": "000001", "name": "平安银行"},
                ]
            },
        )

    def test_get_stock_daily_bars_tool_returns_serialized_bars(self):
        from mcp_stock_server.models.db_models import DailyBar, StockDailyBarsItem
        from mcp_stock_server.models.response_models import GetStockDailyBarsResponse
        from mcp_stock_server.tools.stock_tools import get_stock_daily_bars_tool

        class FakeStockDailyService:
            def get_stock_daily_bars(self, time, codes, limit=120):
                return GetStockDailyBarsResponse(
                    time=time,
                    items=[
                        StockDailyBarsItem(
                            code="600000",
                            daily_bars=[
                                DailyBar(
                                    code="600000",
                                    trade_date=date(2026, 5, 26),
                                    open=Decimal("10.00"),
                                    close=Decimal("10.50"),
                                    high=Decimal("10.60"),
                                    low=Decimal("9.90"),
                                    vol=1000,
                                    amount=Decimal("1000000"),
                                )
                            ],
                        )
                    ],
                )

        payload = get_stock_daily_bars_tool(
            FakeStockDailyService(),
            {"time": "2026-05-26", "codes": ["600000"]},
        )

        self.assertEqual(payload["time"], "2026-05-26")
        self.assertEqual(payload["items"][0]["code"], "600000")
        self.assertEqual(payload["items"][0]["daily_bars"][0]["close"], "10.50")

    def test_upsert_stock_daily_bars_tool_returns_summary(self):
        from mcp_stock_server.models.response_models import (
            UpsertErrorItem,
            UpsertStockDailyBarsResponse,
        )
        from mcp_stock_server.tools.stock_tools import upsert_stock_daily_bars_tool

        class FakeStockDailyService:
            def upsert_stock_daily_bars(self, request):
                return UpsertStockDailyBarsResponse(
                    time=request.time,
                    total=2,
                    success=1,
                    failed=1,
                    errors=[UpsertErrorItem(code="999999", reason="stock code not found")],
                )

        payload = upsert_stock_daily_bars_tool(
            FakeStockDailyService(),
            {
                "time": "2026-05-26",
                "daily_data": [
                    {
                        "code": "600000",
                        "open": "10.00",
                        "close": "10.50",
                        "high": "10.60",
                        "low": "9.90",
                        "vol": 1000,
                        "amount": "1000000",
                        "trade_date": "2026-05-26",
                    },
                    {
                        "code": "999999",
                        "open": "10.00",
                        "close": "10.50",
                        "high": "10.60",
                        "low": "9.90",
                        "vol": 1000,
                        "amount": "1000000",
                        "trade_date": "2026-05-26",
                    },
                ],
            },
        )

        self.assertEqual(payload["total"], 2)
        self.assertEqual(payload["success"], 1)
        self.assertEqual(payload["failed"], 1)
        self.assertEqual(payload["errors"][0]["code"], "999999")

    def test_write_service_splits_insert_and_update_rows(self):
        from mcp_stock_server.models.request_models import UpsertStockDailyBarsRequest
        from mcp_stock_server.services.stock_daily_service import StockDailyService

        class FakeMasterRepository:
            def existing_codes(self, codes):
                return set(codes)

        class FakeDailyRepository:
            def __init__(self):
                self.inserted = []
                self.updated = []

            def existing_daily_keys(self, keys):
                return {("000001", date(2026, 5, 26))}

            def batch_insert(self, rows):
                self.inserted.extend(rows)
                return len(rows)

            def batch_update(self, rows):
                self.updated.extend(rows)
                return len(rows)

        daily_repo = FakeDailyRepository()
        service = StockDailyService(
            stock_master_repository=FakeMasterRepository(),
            stock_daily_repository=daily_repo,
        )
        request = UpsertStockDailyBarsRequest.from_dict(
            {
                "time": "2026-05-26",
                "daily_data": [
                    {
                        "code": "000001",
                        "open": "12.31",
                        "close": "12.58",
                        "high": "12.66",
                        "low": "12.20",
                        "vol": 1532456,
                        "amount": "1923456789",
                        "trade_date": "2026-05-26",
                    },
                    {
                        "code": "000002",
                        "open": "8.11",
                        "close": "8.25",
                        "high": "8.30",
                        "low": "8.05",
                        "vol": 2234567,
                        "amount": "1432456789",
                        "trade_date": "2026-05-26",
                    },
                ],
            }
        )

        response = service.upsert_stock_daily_bars(request)

        self.assertEqual(response.success, 2)
        self.assertEqual(len(daily_repo.updated), 1)
        self.assertEqual(len(daily_repo.inserted), 1)
        self.assertEqual(daily_repo.updated[0].code, "000001")
        self.assertEqual(daily_repo.inserted[0].code, "000002")

    def test_mysql_stock_master_repository_lists_all_codes(self):
        from mcp_stock_server.repositories.stock_master_repository import MySQLStockMasterRepository

        class FakeCursor:
            def __init__(self, rows):
                self.rows = rows
                self.executed = []

            def execute(self, sql, params=None):
                self.executed.append((sql, params))

            def fetchall(self):
                return self.rows

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        class FakeConnection:
            def __init__(self, rows):
                self.rows = rows
                self.cursors = []

            def cursor(self):
                cursor = FakeCursor(self.rows)
                self.cursors.append(cursor)
                return cursor

        connection = FakeConnection(
            [
                {"code": "000001", "name": "平安银行"},
                {"code": "000002", "name": "万科A"},
            ]
        )
        repository = MySQLStockMasterRepository(lambda: connection)

        items = repository.list_all()

        self.assertEqual([item.code for item in items], ["000001", "000002"])
        self.assertIn("SELECT code, name", connection.cursors[0].executed[0][0])

    def test_mysql_stock_daily_repository_existing_daily_keys(self):
        from mcp_stock_server.repositories.stock_daily_repository import MySQLStockDailyRepository

        class FakeCursor:
            def __init__(self, rows):
                self.rows = rows
                self.executed = []

            def execute(self, sql, params=None):
                self.executed.append((sql, params))

            def fetchall(self):
                return self.rows

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        class FakeConnection:
            def __init__(self, rows):
                self.rows = rows
                self.cursors = []

            def cursor(self):
                cursor = FakeCursor(self.rows)
                self.cursors.append(cursor)
                return cursor

        connection = FakeConnection(
            [
                {"stock_code": "600000", "trade_date": date(2026, 5, 26)},
            ]
        )
        repository = MySQLStockDailyRepository(lambda: connection)

        result = repository.existing_daily_keys([("600000", date(2026, 5, 26))])

        self.assertEqual(result, {("600000", date(2026, 5, 26))})
        self.assertIn("SELECT stock_code, trade_date", connection.cursors[0].executed[0][0])

    def test_mysql_stock_daily_repository_batch_insert_builds_params(self):
        from mcp_stock_server.models.request_models import UpsertStockDailyDataItem
        from mcp_stock_server.repositories.stock_daily_repository import MySQLStockDailyRepository

        class FakeCursor:
            def __init__(self):
                self.executemany_calls = []

            def executemany(self, sql, params):
                self.executemany_calls.append((sql, params))

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        class FakeConnection:
            def __init__(self):
                self.cursor_obj = FakeCursor()
                self.committed = False

            def cursor(self):
                return self.cursor_obj

            def commit(self):
                self.committed = True

        connection = FakeConnection()
        repository = MySQLStockDailyRepository(lambda: connection)

        rows = [
            UpsertStockDailyDataItem(
                code="600000",
                open=Decimal("10.00"),
                close=Decimal("10.50"),
                high=Decimal("10.60"),
                low=Decimal("9.90"),
                vol=1000,
                amount=Decimal("1000000"),
                trade_date=date(2026, 5, 26),
            )
        ]

        inserted = repository.batch_insert(rows)

        self.assertEqual(inserted, 1)
        self.assertTrue(connection.committed)
        sql, params = connection.cursor_obj.executemany_calls[0]
        self.assertIn("INSERT INTO stock_daily", sql)
        self.assertEqual(params[0][0], "600000")

    def test_build_mysql_services_uses_mysql_repositories(self):
        from mcp_stock_server.db.mysql import MySQLConfig
        from mcp_stock_server.main import build_mysql_services
        from mcp_stock_server.repositories.stock_daily_repository import (
            MySQLStockDailyRepository,
        )
        from mcp_stock_server.repositories.stock_master_repository import (
            MySQLStockMasterRepository,
        )

        captured = {}

        def fake_connection_factory(config):
            captured["config"] = config

            def _connect():
                return object()

            return _connect

        config = MySQLConfig(
            host="127.0.0.1",
            port=3306,
            user="root",
            password="secret",
            database="stocks",
        )

        stock_master_service, stock_daily_service = build_mysql_services(
            config=config,
            connection_factory_builder=fake_connection_factory,
        )

        self.assertIs(captured["config"], config)
        self.assertIsInstance(stock_master_service.stock_master_repository, MySQLStockMasterRepository)
        self.assertIsInstance(stock_daily_service.stock_master_repository, MySQLStockMasterRepository)
        self.assertIsInstance(stock_daily_service.stock_daily_repository, MySQLStockDailyRepository)

    def test_stock_master_service_initializes_master_rows(self):
        from mcp_stock_server.models.db_models import StockCodeItem
        from mcp_stock_server.services.stock_master_service import StockMasterService

        class FakeMasterRepository:
            def __init__(self):
                self.inserted = []

            def list_all(self):
                return []

            def existing_codes(self, codes):
                return set()

            def batch_insert(self, rows):
                self.inserted.extend(rows)
                return len(rows)

        repository = FakeMasterRepository()
        service = StockMasterService(stock_master_repository=repository)

        inserted = service.initialize_stock_master(
            [
                {"code": "600000", "name": "浦发银行"},
                {"code": "000001", "name": "平安银行"},
            ]
        )

        self.assertEqual(inserted, 2)
        self.assertEqual(
            repository.inserted,
            [
                StockCodeItem(code="600000", name="浦发银行"),
                StockCodeItem(code="000001", name="平安银行"),
            ],
        )

    def test_init_script_initializes_when_stock_master_is_empty(self):
        from mcp_stock_server.init_stock_master import initialize_if_empty

        class FakeStockMasterService:
            def __init__(self):
                self.initialized = False

            def list_stock_codes(self):
                return []

            def initialize_stock_master(self, rows):
                self.initialized = True
                return len(list(rows))

        inserted = initialize_if_empty(
            stock_master_service=FakeStockMasterService(),
            fetch_rows=lambda: [
                {"code": "600000", "name": "浦发银行"},
                {"code": "000001", "name": "平安银行"},
            ],
        )

        self.assertEqual(inserted, 2)

    def test_init_script_skips_when_stock_master_not_empty(self):
        from mcp_stock_server.init_stock_master import initialize_if_empty
        from mcp_stock_server.models.db_models import StockCodeItem

        class FakeStockMasterService:
            def __init__(self):
                self.initialized = False

            def list_stock_codes(self):
                return [StockCodeItem(code="600000", name="浦发银行")]

            def initialize_stock_master(self, rows):
                self.initialized = True
                return len(list(rows))

        service = FakeStockMasterService()
        inserted = initialize_if_empty(
            stock_master_service=service,
            fetch_rows=lambda: [
                {"code": "000001", "name": "平安银行"},
            ],
        )

        self.assertEqual(inserted, 0)
        self.assertFalse(service.initialized)

    def test_stock_daily_init_script_initializes_rows(self):
        from mcp_stock_server.init_stock_daily import initialize_stock_daily

        class FakeStockDailyService:
            def __init__(self):
                self.loaded = None

            def upsert_stock_daily_bars(self, request):
                self.loaded = request
                return type("Result", (), {"success": len(request.daily_data)})()

        inserted = initialize_stock_daily(
            stock_daily_service=FakeStockDailyService(),
            codes=["600000"],
            fetch_rows=lambda symbols: [
                {
                    "code": "600000",
                    "open": "10.00",
                    "close": "10.50",
                    "high": "10.60",
                    "low": "9.90",
                    "vol": 1000,
                    "amount": "1000000",
                    "trade_date": "2026-05-26",
                }
            ],
        )

        self.assertEqual(inserted, 1)

    def test_mysql_config_loads_from_json_file(self):
        from mcp_stock_server.db.mysql import MySQLConfig

        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.json"
            path.write_text(
                (
                    '{'
                    '"mysql": {'
                    '"host": "127.0.0.1",'
                    '"port": 3306,'
                    '"user": "root",'
                    '"password": "secret",'
                    '"database": "stocks"'
                    "}"
                    "}"
                ),
                encoding="utf-8",
            )

            config = MySQLConfig.from_file(path)

        self.assertEqual(config.host, "127.0.0.1")
        self.assertEqual(config.port, 3306)
        self.assertEqual(config.user, "root")
        self.assertEqual(config.password, "secret")
        self.assertEqual(config.database, "stocks")

    def test_build_mysql_services_loads_config_from_file_by_default(self):
        from mcp_stock_server.main import build_mysql_services
        from mcp_stock_server.repositories.stock_daily_repository import (
            MySQLStockDailyRepository,
        )
        from mcp_stock_server.repositories.stock_master_repository import (
            MySQLStockMasterRepository,
        )

        captured = {}

        def fake_connection_factory(config):
            captured["config"] = config

            def _connect():
                return object()

            return _connect

        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.json"
            path.write_text(
                (
                    '{'
                    '"mysql": {'
                    '"host": "db.local",'
                    '"port": 3307,'
                    '"user": "tester",'
                    '"password": "pw",'
                    '"database": "stocks_dev"'
                    "}"
                    "}"
                ),
                encoding="utf-8",
            )

            stock_master_service, stock_daily_service = build_mysql_services(
                config_path=path,
                connection_factory_builder=fake_connection_factory,
            )

        self.assertEqual(captured["config"].host, "db.local")
        self.assertEqual(captured["config"].port, 3307)
        self.assertEqual(captured["config"].user, "tester")
        self.assertEqual(captured["config"].database, "stocks_dev")
        self.assertIsInstance(stock_master_service.stock_master_repository, MySQLStockMasterRepository)
        self.assertIsInstance(stock_daily_service.stock_master_repository, MySQLStockMasterRepository)
        self.assertIsInstance(stock_daily_service.stock_daily_repository, MySQLStockDailyRepository)

    def test_create_mcp_server_registers_three_tools(self):
        if importlib.util.find_spec("mcp") is None:
            self.skipTest("mcp package is not installed in the current test environment")
        from mcp_stock_server.server import create_mcp_server

        class FakeQueryService:
            def list_stock_codes(self):
                from mcp_stock_server.models.db_models import StockCodeItem

                return [StockCodeItem(code="000001", name="平安银行")]

        class FakeWriteService:
            pass

        class FakeFastMCP:
            def __init__(self, name):
                self.name = name
                self.registered = {}

            def tool(self, name=None, description=None):
                def decorator(func):
                    self.registered[name or func.__name__] = {
                        "func": func,
                        "description": description,
                    }
                    return func

                return decorator

        app = create_mcp_server(
            FakeQueryService(),
            FakeWriteService(),
            fastmcp_cls=FakeFastMCP,
        )

        self.assertEqual(app.name, "mcp-stock-server")
        self.assertEqual(
            sorted(app.registered.keys()),
            [
                "get_stock_daily_bars",
                "list_stock_codes",
                "upsert_stock_daily_bars",
            ],
        )

    def test_registered_tool_returns_expected_payload(self):
        if importlib.util.find_spec("mcp") is None:
            self.skipTest("mcp package is not installed in the current test environment")
        from mcp_stock_server.server import create_mcp_server

        class FakeQueryService:
            def list_stock_codes(self):
                from mcp_stock_server.models.db_models import StockCodeItem

                return [StockCodeItem(code="000001", name="平安银行")]

        class FakeWriteService:
            pass

        class FakeFastMCP:
            def __init__(self, name):
                self.name = name
                self.registered = {}

            def tool(self, name=None, description=None):
                def decorator(func):
                    self.registered[name or func.__name__] = func
                    return func

                return decorator

        app = create_mcp_server(
            FakeQueryService(),
            FakeWriteService(),
            fastmcp_cls=FakeFastMCP,
        )
        payload = app.registered["list_stock_codes"]()

        self.assertEqual(
            payload,
            {"items": [{"code": "000001", "name": "平安银行"}]},
        )


if __name__ == "__main__":
    unittest.main()
