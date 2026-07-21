import unittest
import importlib.util
import threading
from dataclasses import dataclass
from datetime import date
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


class MCPStockServerTests(unittest.TestCase):
    def test_mcp_runtime_config_defaults_when_mcp_section_missing(self):
        from mcp_stock_server.main import MCPRuntimeConfig

        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.json"
            path.write_text(
                '{"mysql":{"host":"127.0.0.1","port":3306,"user":"u","password":"p","database":"stocks"}}',
                encoding="utf-8",
            )

            config = MCPRuntimeConfig.from_file(path)

        self.assertEqual(config.transport, "stdio")
        self.assertEqual(config.host, "127.0.0.1")
        self.assertEqual(config.port, 8000)
        self.assertEqual(config.streamable_http_path, "/mcp")

    def test_mcp_runtime_config_loads_http_settings_from_file(self):
        from mcp_stock_server.main import MCPRuntimeConfig

        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.json"
            path.write_text(
                (
                    '{"mysql":{"host":"127.0.0.1","port":3306,"user":"u","password":"p","database":"stocks"},'
                    '"mcp":{"transport":"streamable-http","host":"127.0.0.1","port":9001,"streamable_http_path":"/stocks-mcp"}}'
                ),
                encoding="utf-8",
            )

            config = MCPRuntimeConfig.from_file(path)

        self.assertEqual(config.transport, "streamable-http")
        self.assertEqual(config.host, "127.0.0.1")
        self.assertEqual(config.port, 9001)
        self.assertEqual(config.streamable_http_path, "/stocks-mcp")

    def test_mcp_runtime_config_defaults_task_store_backend_to_memory(self):
        from mcp_stock_server.main import MCPRuntimeConfig

        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.json"
            path.write_text(
                '{"mysql":{"host":"127.0.0.1","port":3306,"user":"u","password":"p","database":"stocks"}}',
                encoding="utf-8",
            )

            config = MCPRuntimeConfig.from_file(path)

        self.assertEqual(config.task_store_backend, "memory")

    def test_mcp_runtime_config_loads_task_store_backend_from_file(self):
        from mcp_stock_server.main import MCPRuntimeConfig

        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.json"
            path.write_text(
                (
                    '{"mysql":{"host":"127.0.0.1","port":3306,"user":"u","password":"p","database":"stocks"},'
                    '"mcp":{"tasks":{"store_backend":"mysql"}}}'
                ),
                encoding="utf-8",
            )

            config = MCPRuntimeConfig.from_file(path)

        self.assertEqual(config.task_store_backend, "mysql")

    def test_mcp_runtime_config_defaults_recovery_enabled_to_true(self):
        from mcp_stock_server.main import MCPRuntimeConfig

        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.json"
            path.write_text(
                '{"mysql":{"host":"127.0.0.1","port":3306,"user":"u","password":"p","database":"stocks"}}',
                encoding="utf-8",
            )

            config = MCPRuntimeConfig.from_file(path)

        self.assertTrue(config.recovery_enabled)

    def test_mcp_runtime_config_loads_recovery_enabled_from_file(self):
        from mcp_stock_server.main import MCPRuntimeConfig

        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.json"
            path.write_text(
                (
                    '{"mysql":{"host":"127.0.0.1","port":3306,"user":"u","password":"p","database":"stocks"},'
                    '"mcp":{"tasks":{"store_backend":"mysql","recovery":{"enabled":false}}}}'
                ),
                encoding="utf-8",
            )

            config = MCPRuntimeConfig.from_file(path)

        self.assertFalse(config.recovery_enabled)

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

    def test_inmemory_daily_repository_returns_recent_rows_in_ascending_trade_date(self):
        from mcp_stock_server.models.db_models import DailyBar
        from mcp_stock_server.repositories.stock_daily_repository import InMemoryStockDailyRepository

        repository = InMemoryStockDailyRepository(
            items=[
                DailyBar(
                    code="600000",
                    trade_date=date(2026, 5, 24),
                    open=Decimal("10.00"),
                    close=Decimal("10.10"),
                    high=Decimal("10.20"),
                    low=Decimal("9.90"),
                    vol=1000,
                    amount=Decimal("1000000"),
                ),
                DailyBar(
                    code="600000",
                    trade_date=date(2026, 5, 25),
                    open=Decimal("10.10"),
                    close=Decimal("10.20"),
                    high=Decimal("10.30"),
                    low=Decimal("10.00"),
                    vol=1000,
                    amount=Decimal("1000000"),
                ),
                DailyBar(
                    code="600000",
                    trade_date=date(2026, 5, 26),
                    open=Decimal("10.20"),
                    close=Decimal("10.30"),
                    high=Decimal("10.40"),
                    low=Decimal("10.10"),
                    vol=1000,
                    amount=Decimal("1000000"),
                ),
            ]
        )

        rows = repository.get_recent_bars_by_codes(
            as_of=date(2026, 5, 26),
            codes=["600000"],
            limit=2,
        )

        self.assertEqual([row.trade_date.isoformat() for row in rows], ["2026-05-25", "2026-05-26"])

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

        self.assertEqual(payload, ["600000", "000001"])

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

    def test_screen_b1_stocks_tool_returns_selected_code_with_name_defaulting_to_none(self):
        from mcp_stock_server.tools.stock_tools import screen_b1_stocks_tool

        class FakeStockMasterService:
            def list_stock_codes(self):
                return ["000001", "000002", "000003"]

        class FakeStockDailyService:
            def __init__(self):
                self.calls = []

            def get_stock_daily_bars(self, time, codes, limit=120):
                self.calls.append(("bars", list(codes)))
                return type(
                    "Response",
                    (),
                    {
                        "time": time,
                        "items": [
                            type(
                                "Item",
                                (),
                                {
                                    "code": "000001",
                                    "daily_bars": [
                                        type("Bar", (), {"close": "10.50"})(),
                                    ],
                                },
                            )()
                        ],
                    },
                )()

        payload = screen_b1_stocks_tool(
            stock_master_service=FakeStockMasterService(),
            stock_daily_service=FakeStockDailyService(),
            payload={"time": "2026-05-26"},
            compute_amplitude_fn=lambda stock_daily_service, time, codes, limit=120: {
                "time": time,
                "items": [
                    {"code": "000001", "value": 6.5},
                    {"code": "000002", "value": 8.1},
                    {"code": "000003", "value": 6.8},
                ],
            },
            compute_kdj_fn=lambda stock_daily_service, time, codes, period=9, smooth_k=3, smooth_d=3, limit=120: {
                "time": time,
                "items": [
                    {"code": "000001", "j": [30.0, 18.0]},
                    {"code": "000003", "j": [40.0, 25.0]},
                ],
            },
            compute_multi_trend_fn=lambda stock_daily_service, time, codes, periods=None, limit=120: {
                "time": time,
                "items": [
                    {"code": "000001", "values": [9.80, 10.10]},
                ],
            },
            compute_short_trend_fn=lambda stock_daily_service, time, codes, period=10, limit=120: {
                "time": time,
                "items": [
                    {"code": "000001", "values": [10.00, 10.30]},
                ],
            },
            get_bars_fn=lambda stock_daily_service, payload: {
                "time": payload["time"],
                "items": [
                    {
                        "code": "000001",
                        "daily_bars": [
                            {"close": "10.50"},
                        ],
                    }
                ],
            },
        )

        self.assertEqual(payload["time"], "2026-05-26")
        self.assertEqual(payload["total_candidates"], 3)
        self.assertEqual(payload["selected_count"], 1)
        self.assertEqual(payload["items"], [{"code": "000001", "name": None}])

    def test_screen_b1_stocks_tool_short_circuits_when_first_step_empty(self):
        from mcp_stock_server.tools.stock_tools import screen_b1_stocks_tool

        class FakeStockMasterService:
            def list_stock_codes(self):
                return ["000001", "000002"]

        class FakeStockDailyService:
            pass

        def _unexpected(*args, **kwargs):
            raise AssertionError("later steps should not be called after short circuit")

        payload = screen_b1_stocks_tool(
            stock_master_service=FakeStockMasterService(),
            stock_daily_service=FakeStockDailyService(),
            payload={"time": "2026-05-26"},
            compute_amplitude_fn=lambda stock_daily_service, time, codes, limit=120: {
                "time": time,
                "items": [
                    {"code": "000001", "value": 7.2},
                    {"code": "000002", "value": 8.1},
                ],
            },
            compute_kdj_fn=_unexpected,
            compute_multi_trend_fn=_unexpected,
            compute_short_trend_fn=_unexpected,
            get_bars_fn=_unexpected,
        )

        self.assertEqual(payload["total_candidates"], 2)
        self.assertEqual(payload["selected_count"], 0)
        self.assertEqual(payload["items"], [])

    def test_screen_b1_stocks_tool_accepts_stock_code_items_from_master_service(self):
        from mcp_stock_server.models.db_models import StockCodeItem
        from mcp_stock_server.tools.stock_tools import screen_b1_stocks_tool

        class FakeStockMasterService:
            def list_stock_codes(self):
                return [
                    StockCodeItem(code="000001", name="平安银行"),
                    StockCodeItem(code="000002", name="万科A"),
                    StockCodeItem(code="000003", name="*ST中迪"),
                    StockCodeItem(code="000004", name="ST旭电"),
                ]

        class FakeStockDailyService:
            pass

        observed_codes: list[list[str]] = []

        def _capture_codes(stock_daily_service, time, codes, **kwargs):
            observed_codes.append(list(codes))
            return {
                "time": time,
                "items": [
                    {"code": "000001", "value": 6.5},
                    {"code": "000002", "value": 7.8},
                ],
            }

        payload = screen_b1_stocks_tool(
            stock_master_service=FakeStockMasterService(),
            stock_daily_service=FakeStockDailyService(),
            payload={"time": "2026-05-26"},
            compute_amplitude_fn=_capture_codes,
            compute_kdj_fn=lambda stock_daily_service, time, codes, period=9, smooth_k=3, smooth_d=3, limit=120: {
                "time": time,
                "items": [
                    {"code": "000001", "j": [30.0, 18.0]},
                ],
            },
            compute_multi_trend_fn=lambda stock_daily_service, time, codes, periods=None, limit=120: {
                "time": time,
                "items": [
                    {"code": "000001", "values": [9.80, 10.10]},
                ],
            },
            compute_short_trend_fn=lambda stock_daily_service, time, codes, period=10, limit=120: {
                "time": time,
                "items": [
                    {"code": "000001", "values": [10.00, 10.30]},
                ],
            },
            get_bars_fn=lambda stock_daily_service, payload: {
                "time": payload["time"],
                "items": [
                    {
                        "code": "000001",
                        "daily_bars": [
                            {"close": "10.50"},
                        ],
                    }
                ],
            },
        )

        self.assertEqual(payload["items"], [{"code": "000001", "name": "平安银行"}])
        self.assertEqual(observed_codes, [["000001", "000002"]])
        self.assertEqual(payload["total_candidates"], 2)
        self.assertEqual(payload["selected_count"], 1)

    def test_compute_short_trend_tool_returns_series(self):
        from mcp_stock_server.tools.indicator_tools import compute_short_trend_tool

        payload = compute_short_trend_tool(closes=[10.0, 11.0, 12.0], period=2)

        self.assertEqual(len(payload["values"]), 3)
        self.assertAlmostEqual(payload["values"][0], 10.0)

    def test_compute_multi_trend_tool_preserves_none_before_windows_fill(self):
        from mcp_stock_server.tools.indicator_tools import compute_multi_trend_tool

        payload = compute_multi_trend_tool(
            closes=[10.0, 11.0, 12.0, 13.0],
            periods=[2, 3],
        )

        self.assertEqual(payload["values"][0], None)
        self.assertEqual(payload["values"][1], None)
        self.assertAlmostEqual(payload["values"][2], 11.25)
        self.assertAlmostEqual(payload["values"][3], 12.25)

    def test_compute_kdj_tool_returns_k_d_j_series(self):
        from mcp_stock_server.tools.indicator_tools import compute_kdj_tool

        payload = compute_kdj_tool(
            highs=[10.0, 11.0, 12.0, 13.0],
            lows=[8.0, 8.5, 9.0, 9.5],
            closes=[9.0, 10.0, 11.0, 12.0],
            period=2,
            smooth_k=3,
            smooth_d=3,
        )

        self.assertEqual(sorted(payload.keys()), ["d", "j", "k"])
        self.assertEqual(len(payload["k"]), 4)
        self.assertEqual(payload["k"][0], None)
        self.assertIsNotNone(payload["j"][-1])

    def test_compute_amplitude_tool_returns_series(self):
        from mcp_stock_server.tools.indicator_tools import compute_amplitude_tool

        payload = compute_amplitude_tool(
            highs=[10.0, 11.0, 12.0],
            lows=[9.0, 10.0, 10.5],
            closes=[9.5, 10.0, 11.0],
        )

        self.assertEqual(payload["values"][0], None)
        self.assertAlmostEqual(payload["values"][1], (11.0 - 10.0) / 9.5 * 100)
        self.assertAlmostEqual(payload["values"][2], (12.0 - 10.5) / 10.0 * 100)

    def test_compute_short_trend_by_code_tool_loads_bars_from_service(self):
        from datetime import date
        from decimal import Decimal
        from mcp_stock_server.models.db_models import DailyBar, StockDailyBarsItem
        from mcp_stock_server.models.response_models import GetStockDailyBarsResponse
        from mcp_stock_server.tools.indicator_tools import compute_short_trend_by_code_tool

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
                                    trade_date=date(2026, 5, 25),
                                    open=Decimal("10.00"),
                                    close=Decimal("10.50"),
                                    high=Decimal("10.60"),
                                    low=Decimal("9.90"),
                                    vol=1000,
                                    amount=Decimal("1000000"),
                                ),
                                DailyBar(
                                    code="600000",
                                    trade_date=date(2026, 5, 26),
                                    open=Decimal("10.50"),
                                    close=Decimal("10.80"),
                                    high=Decimal("10.90"),
                                    low=Decimal("10.40"),
                                    vol=1200,
                                    amount=Decimal("1100000"),
                                ),
                            ],
                        ),
                        StockDailyBarsItem(
                            code="000001",
                            daily_bars=[
                                DailyBar(
                                    code="000001",
                                    trade_date=date(2026, 5, 25),
                                    open=Decimal("8.00"),
                                    close=Decimal("8.20"),
                                    high=Decimal("8.30"),
                                    low=Decimal("7.90"),
                                    vol=800,
                                    amount=Decimal("800000"),
                                ),
                                DailyBar(
                                    code="000001",
                                    trade_date=date(2026, 5, 26),
                                    open=Decimal("8.20"),
                                    close=Decimal("8.50"),
                                    high=Decimal("8.60"),
                                    low=Decimal("8.10"),
                                    vol=850,
                                    amount=Decimal("830000"),
                                ),
                            ],
                        ),
                    ],
                )

        payload = compute_short_trend_by_code_tool(
            FakeStockDailyService(),
            time="2026-05-26",
            codes=["600000", "000001"],
            period=2,
            limit=20,
        )

        self.assertEqual(payload["time"], "2026-05-26")
        self.assertEqual([item["code"] for item in payload["items"]], ["600000", "000001"])
        self.assertEqual(len(payload["items"][0]["values"]), 2)
        self.assertEqual(len(payload["items"][1]["values"]), 2)

    def test_compute_multi_trend_by_code_tool_loads_bars_from_service(self):
        from datetime import date
        from decimal import Decimal
        from mcp_stock_server.models.db_models import DailyBar, StockDailyBarsItem
        from mcp_stock_server.models.response_models import GetStockDailyBarsResponse
        from mcp_stock_server.tools.indicator_tools import compute_multi_trend_by_code_tool

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
                                    trade_date=date(2026, 5, 23),
                                    open=Decimal("10.00"),
                                    close=Decimal("10.00"),
                                    high=Decimal("10.10"),
                                    low=Decimal("9.90"),
                                    vol=1000,
                                    amount=Decimal("1000000"),
                                ),
                                DailyBar(
                                    code="600000",
                                    trade_date=date(2026, 5, 24),
                                    open=Decimal("10.00"),
                                    close=Decimal("11.00"),
                                    high=Decimal("11.10"),
                                    low=Decimal("9.95"),
                                    vol=1000,
                                    amount=Decimal("1000000"),
                                ),
                                DailyBar(
                                    code="600000",
                                    trade_date=date(2026, 5, 25),
                                    open=Decimal("11.00"),
                                    close=Decimal("12.00"),
                                    high=Decimal("12.10"),
                                    low=Decimal("10.90"),
                                    vol=1000,
                                    amount=Decimal("1000000"),
                                ),
                                DailyBar(
                                    code="600000",
                                    trade_date=date(2026, 5, 26),
                                    open=Decimal("12.00"),
                                    close=Decimal("13.00"),
                                    high=Decimal("13.10"),
                                    low=Decimal("11.90"),
                                    vol=1000,
                                    amount=Decimal("1000000"),
                                ),
                            ],
                        ),
                        StockDailyBarsItem(
                            code="000001",
                            daily_bars=[
                                DailyBar(
                                    code="000001",
                                    trade_date=date(2026, 5, 23),
                                    open=Decimal("8.00"),
                                    close=Decimal("8.00"),
                                    high=Decimal("8.10"),
                                    low=Decimal("7.90"),
                                    vol=800,
                                    amount=Decimal("800000"),
                                ),
                                DailyBar(
                                    code="000001",
                                    trade_date=date(2026, 5, 24),
                                    open=Decimal("8.00"),
                                    close=Decimal("9.00"),
                                    high=Decimal("9.10"),
                                    low=Decimal("7.95"),
                                    vol=820,
                                    amount=Decimal("820000"),
                                ),
                                DailyBar(
                                    code="000001",
                                    trade_date=date(2026, 5, 25),
                                    open=Decimal("9.00"),
                                    close=Decimal("10.00"),
                                    high=Decimal("10.10"),
                                    low=Decimal("8.90"),
                                    vol=840,
                                    amount=Decimal("840000"),
                                ),
                                DailyBar(
                                    code="000001",
                                    trade_date=date(2026, 5, 26),
                                    open=Decimal("10.00"),
                                    close=Decimal("11.00"),
                                    high=Decimal("11.10"),
                                    low=Decimal("9.90"),
                                    vol=860,
                                    amount=Decimal("860000"),
                                ),
                            ],
                        ),
                    ],
                )

        payload = compute_multi_trend_by_code_tool(
            FakeStockDailyService(),
            time="2026-05-26",
            codes=["600000", "000001"],
            periods=[2, 3],
            limit=20,
        )

        self.assertEqual(payload["time"], "2026-05-26")
        self.assertEqual([item["code"] for item in payload["items"]], ["600000", "000001"])
        self.assertEqual(payload["items"][0]["values"][0], None)
        self.assertEqual(payload["items"][1]["values"][0], None)

    def test_compute_kdj_by_code_tool_loads_bars_from_service(self):
        from datetime import date
        from decimal import Decimal
        from mcp_stock_server.models.db_models import DailyBar, StockDailyBarsItem
        from mcp_stock_server.models.response_models import GetStockDailyBarsResponse
        from mcp_stock_server.tools.indicator_tools import compute_kdj_by_code_tool

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
                                    trade_date=date(2026, 5, 23),
                                    open=Decimal("9.00"),
                                    close=Decimal("9.00"),
                                    high=Decimal("10.00"),
                                    low=Decimal("8.00"),
                                    vol=1000,
                                    amount=Decimal("1000000"),
                                ),
                                DailyBar(
                                    code="600000",
                                    trade_date=date(2026, 5, 24),
                                    open=Decimal("9.00"),
                                    close=Decimal("10.00"),
                                    high=Decimal("11.00"),
                                    low=Decimal("8.50"),
                                    vol=1000,
                                    amount=Decimal("1000000"),
                                ),
                                DailyBar(
                                    code="600000",
                                    trade_date=date(2026, 5, 25),
                                    open=Decimal("10.00"),
                                    close=Decimal("11.00"),
                                    high=Decimal("12.00"),
                                    low=Decimal("9.00"),
                                    vol=1000,
                                    amount=Decimal("1000000"),
                                ),
                                DailyBar(
                                    code="600000",
                                    trade_date=date(2026, 5, 26),
                                    open=Decimal("11.00"),
                                    close=Decimal("12.00"),
                                    high=Decimal("13.00"),
                                    low=Decimal("9.50"),
                                    vol=1000,
                                    amount=Decimal("1000000"),
                                ),
                            ],
                        ),
                        StockDailyBarsItem(
                            code="000001",
                            daily_bars=[
                                DailyBar(
                                    code="000001",
                                    trade_date=date(2026, 5, 23),
                                    open=Decimal("8.00"),
                                    close=Decimal("8.10"),
                                    high=Decimal("8.50"),
                                    low=Decimal("7.90"),
                                    vol=900,
                                    amount=Decimal("900000"),
                                ),
                                DailyBar(
                                    code="000001",
                                    trade_date=date(2026, 5, 24),
                                    open=Decimal("8.10"),
                                    close=Decimal("8.30"),
                                    high=Decimal("8.60"),
                                    low=Decimal("8.00"),
                                    vol=950,
                                    amount=Decimal("920000"),
                                ),
                                DailyBar(
                                    code="000001",
                                    trade_date=date(2026, 5, 25),
                                    open=Decimal("8.30"),
                                    close=Decimal("8.40"),
                                    high=Decimal("8.80"),
                                    low=Decimal("8.20"),
                                    vol=980,
                                    amount=Decimal("940000"),
                                ),
                                DailyBar(
                                    code="000001",
                                    trade_date=date(2026, 5, 26),
                                    open=Decimal("8.40"),
                                    close=Decimal("8.70"),
                                    high=Decimal("8.90"),
                                    low=Decimal("8.30"),
                                    vol=1000,
                                    amount=Decimal("960000"),
                                ),
                            ],
                        ),
                    ],
                )

        payload = compute_kdj_by_code_tool(
            FakeStockDailyService(),
            time="2026-05-26",
            codes=["600000", "000001"],
            period=2,
            smooth_k=3,
            smooth_d=3,
            limit=20,
        )

        self.assertEqual(payload["time"], "2026-05-26")
        self.assertEqual([item["code"] for item in payload["items"]], ["600000", "000001"])
        self.assertEqual(len(payload["items"][0]["k"]), 4)
        self.assertEqual(len(payload["items"][1]["j"]), 4)

    def test_compute_amplitude_by_code_tool_returns_latest_value(self):
        from datetime import date
        from decimal import Decimal
        from mcp_stock_server.models.db_models import DailyBar, StockDailyBarsItem
        from mcp_stock_server.models.response_models import GetStockDailyBarsResponse
        from mcp_stock_server.tools.indicator_tools import compute_amplitude_by_code_tool

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
                                    trade_date=date(2026, 5, 25),
                                    open=Decimal("10.00"),
                                    close=Decimal("10.00"),
                                    high=Decimal("10.50"),
                                    low=Decimal("9.80"),
                                    vol=1000,
                                    amount=Decimal("1000000"),
                                ),
                                DailyBar(
                                    code="600000",
                                    trade_date=date(2026, 5, 26),
                                    open=Decimal("10.10"),
                                    close=Decimal("10.20"),
                                    high=Decimal("10.60"),
                                    low=Decimal("9.90"),
                                    vol=1000,
                                    amount=Decimal("1000000"),
                                ),
                            ],
                        ),
                        StockDailyBarsItem(
                            code="000001",
                            daily_bars=[
                                DailyBar(
                                    code="000001",
                                    trade_date=date(2026, 5, 25),
                                    open=Decimal("8.00"),
                                    close=Decimal("8.20"),
                                    high=Decimal("8.40"),
                                    low=Decimal("7.90"),
                                    vol=800,
                                    amount=Decimal("800000"),
                                ),
                                DailyBar(
                                    code="000001",
                                    trade_date=date(2026, 5, 26),
                                    open=Decimal("8.20"),
                                    close=Decimal("8.30"),
                                    high=Decimal("8.80"),
                                    low=Decimal("8.10"),
                                    vol=820,
                                    amount=Decimal("830000"),
                                ),
                            ],
                        ),
                    ],
                )

        payload = compute_amplitude_by_code_tool(
            FakeStockDailyService(),
            time="2026-05-26",
            codes=["600000", "000001"],
            limit=20,
        )

        self.assertEqual(payload["time"], "2026-05-26")
        self.assertEqual([item["code"] for item in payload["items"]], ["600000", "000001"])
        self.assertAlmostEqual(payload["items"][0]["value"], (10.60 - 9.90) / 10.00 * 100)
        self.assertAlmostEqual(payload["items"][1]["value"], (8.80 - 8.10) / 8.20 * 100)

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

    def test_insert_stock_daily_bars_after_close_tool_fetches_and_upserts_today_rows(self):
        from mcp_stock_server.models.response_models import UpsertStockDailyBarsResponse
        from mcp_stock_server.tools.stock_tools import (
            insert_stock_daily_bars_after_close_tool,
        )

        class FakeStockDailyService:
            def __init__(self):
                self.loaded = None

            def upsert_stock_daily_bars(self, request):
                self.loaded = request
                return UpsertStockDailyBarsResponse(
                    time=request.time,
                    total=len(request.daily_data),
                    success=len(request.daily_data),
                    failed=0,
                    errors=[],
                )

        service = FakeStockDailyService()
        payload = insert_stock_daily_bars_after_close_tool(
            service,
            {"time": "2026-05-26"},
            fetch_codes=lambda: [{"code": "600000", "name": "浦发银行"}],
            fetch_rows=lambda codes: [
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

        self.assertEqual(service.loaded.time.isoformat(), "2026-05-26")
        self.assertEqual(len(service.loaded.daily_data), 1)
        self.assertEqual(service.loaded.daily_data[0].code, "600000")
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["success"], 1)
        self.assertEqual(payload["failed"], 0)

    def test_write_service_upserts_rows(self):
        from mcp_stock_server.models.request_models import UpsertStockDailyBarsRequest
        from mcp_stock_server.services.stock_daily_service import StockDailyService

        class FakeMasterRepository:
            def existing_codes(self, codes):
                return set(codes)

        class FakeDailyRepository:
            def __init__(self):
                self.upserted = []

            def batch_upsert(self, rows):
                self.upserted.extend(rows)
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
        self.assertEqual([item.code for item in daily_repo.upserted], ["000001", "000002"])

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

    def test_mysql_stock_daily_repository_queries_recent_rows_in_ascending_trade_date(self):
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

        connection = FakeConnection([])
        repository = MySQLStockDailyRepository(lambda: connection)

        repository.get_recent_bars_by_codes(
            as_of=date(2026, 5, 26),
            codes=["600000"],
            limit=2,
        )

        executed_sql = connection.cursors[0].executed[0][0]
        self.assertIn("ORDER BY stock_code, trade_date ASC", executed_sql)

    def test_mysql_stock_daily_repository_batch_upsert_builds_params(self):
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

        inserted = repository.batch_upsert(rows)

        self.assertEqual(inserted, 1)
        self.assertTrue(connection.committed)
        sql, params = connection.cursor_obj.executemany_calls[0]
        self.assertIn("INSERT INTO stock_daily", sql)
        self.assertIn("ON DUPLICATE KEY UPDATE", sql)
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

    def test_build_task_store_returns_none_for_memory_backend(self):
        from mcp_stock_server.db.mysql import MySQLConfig
        from mcp_stock_server.main import MCPRuntimeConfig, build_task_store

        runtime_config = MCPRuntimeConfig(task_store_backend="memory")
        mysql_config = MySQLConfig(
            host="127.0.0.1",
            port=3306,
            user="u",
            password="p",
            database="stocks",
        )

        self.assertIsNone(build_task_store(runtime_config, mysql_config))

    def test_build_task_store_initializes_mysql_backend(self):
        from mcp_stock_server.db.mysql import MySQLConfig
        from mcp_stock_server.main import MCPRuntimeConfig, build_task_store

        runtime_config = MCPRuntimeConfig(task_store_backend="mysql")
        mysql_config = MySQLConfig(
            host="127.0.0.1",
            port=3306,
            user="u",
            password="p",
            database="stocks",
        )

        class FakeTaskStore:
            instances = []

            def __init__(self, connection_factory):
                self.connection_factory = connection_factory
                self.ensure_schema_called = 0
                self.reconcile_called = 0
                FakeTaskStore.instances.append(self)

            async def ensure_schema(self):
                self.ensure_schema_called += 1

            async def reconcile_orphaned_tasks(self):
                self.reconcile_called += 1

        with patch("mcp_stock_server.main.MySQLTaskStore", FakeTaskStore):
            store = build_task_store(runtime_config, mysql_config)

        self.assertIs(store, FakeTaskStore.instances[0])
        self.assertEqual(store.ensure_schema_called, 1)
        self.assertEqual(store.reconcile_called, 0)

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
        from mcp_stock_server.init.init_stock_master import initialize_if_empty

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
        from mcp_stock_server.init.init_stock_master import initialize_if_empty
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
        from mcp_stock_server.init.init_stock_daily import initialize_stock_daily

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

    def test_create_mcp_server_registers_expected_tools(self):
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
                "compute_amplitude",
                "compute_kdj",
                "compute_multi_trend",
                "compute_short_trend",
                "get_capability_manifest",
                "get_stock_daily_bars",
                "get_technical_snapshot",
                "insert_stock_daily_bars_after_close",
                "list_stock_codes",
                "screen_b1_stocks",
                "upsert_stock_daily_bars",
            ],
        )

    def test_create_mcp_server_manifest_defaults_to_stdio_transport(self):
        if importlib.util.find_spec("mcp") is None:
            self.skipTest("mcp package is not installed in the current test environment")
        from mcp_stock_server.server import create_mcp_server

        class FakeQueryService:
            def list_stock_codes(self):
                return []

        class FakeWriteService:
            def get_stock_daily_bars(self, time, codes, limit=120):
                return type("Response", (), {"time": time, "items": []})()

            def upsert_stock_daily_bars(self, request):
                return type(
                    "Response",
                    (),
                    {"time": request.time, "total": 0, "success": 0, "failed": 0, "errors": []},
                )()

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

        self.assertEqual(app.capability_manifest["transport"], "stdio")
        self.assertIn("tasks", app.capability_manifest)
        self.assertFalse(app.capability_manifest["tasks"]["enabled"])
        self.assertIn(
            "insert_stock_daily_bars_after_close",
            app.capability_manifest["tasks"]["task_aware_tools"],
        )

    def test_create_mcp_server_manifest_uses_http_transport_when_configured(self):
        if importlib.util.find_spec("mcp") is None:
            self.skipTest("mcp package is not installed in the current test environment")
        from mcp_stock_server.server import create_mcp_server

        class FakeQueryService:
            def list_stock_codes(self):
                return []

        class FakeWriteService:
            def get_stock_daily_bars(self, time, codes, limit=120):
                return type("Response", (), {"time": time, "items": []})()

            def upsert_stock_daily_bars(self, request):
                return type(
                    "Response",
                    (),
                    {"time": request.time, "total": 0, "success": 0, "failed": 0, "errors": []},
                )()

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
            transport="streamable-http",
        )

        self.assertEqual(app.capability_manifest["transport"], "streamable-http")
        self.assertFalse(app.capability_manifest["tasks"]["enabled"])

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

        self.assertEqual(payload, ["000001"])

    def test_registered_b1_screener_tool_returns_selected_codes(self):
        if importlib.util.find_spec("mcp") is None:
            self.skipTest("mcp package is not installed in the current test environment")
        from mcp_stock_server.server import create_mcp_server

        class FakeQueryService:
            def list_stock_codes(self):
                return ["000001", "000002"]

        class FakeWriteService:
            def get_stock_daily_bars(self, time, codes, limit=120):
                from datetime import date
                from decimal import Decimal
                from mcp_stock_server.models.db_models import DailyBar, StockDailyBarsItem
                from mcp_stock_server.models.response_models import GetStockDailyBarsResponse

                return GetStockDailyBarsResponse(
                    time=time,
                    items=[
                        StockDailyBarsItem(
                            code="000001",
                            daily_bars=[
                                DailyBar(
                                    code="000001",
                                    trade_date=date(2026, 5, 26),
                                    open=Decimal("10.00"),
                                    close=Decimal("10.50"),
                                    high=Decimal("10.60"),
                                    low=Decimal("9.90"),
                                    vol=1000,
                                    amount=Decimal("1000000"),
                                ),
                            ],
                        ),
                        StockDailyBarsItem(
                            code="000002",
                            daily_bars=[
                                DailyBar(
                                    code="000002",
                                    trade_date=date(2026, 5, 26),
                                    open=Decimal("9.00"),
                                    close=Decimal("9.10"),
                                    high=Decimal("9.20"),
                                    low=Decimal("8.90"),
                                    vol=900,
                                    amount=Decimal("900000"),
                                ),
                            ],
                        ),
                    ],
                )

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
        payload = app.registered["screen_b1_stocks"]("2026-05-26")

        self.assertEqual(payload["time"], "2026-05-26")
        self.assertIn("selected_count", payload)
        self.assertIn("items", payload)
        self.assertEqual(payload["items"], [{"code": "000001", "name": "平安银行"}])

    def test_registered_indicator_tool_returns_expected_payload(self):
        if importlib.util.find_spec("mcp") is None:
            self.skipTest("mcp package is not installed in the current test environment")
        from mcp_stock_server.server import create_mcp_server

        class FakeQueryService:
            def list_stock_codes(self):
                return []

        class FakeWriteService:
            def get_stock_daily_bars(self, time, codes, limit=120):
                from datetime import date
                from decimal import Decimal
                from mcp_stock_server.models.db_models import DailyBar, StockDailyBarsItem
                from mcp_stock_server.models.response_models import GetStockDailyBarsResponse

                return GetStockDailyBarsResponse(
                    time=time,
                    items=[
                        StockDailyBarsItem(
                            code="000001",
                            daily_bars=[
                                DailyBar(
                                    code="000001",
                                    trade_date=date(2026, 5, 25),
                                    open=Decimal("10.00"),
                                    close=Decimal("10.50"),
                                    high=Decimal("10.60"),
                                    low=Decimal("9.90"),
                                    vol=1000,
                                    amount=Decimal("1000000"),
                                ),
                                DailyBar(
                                    code="000001",
                                    trade_date=date(2026, 5, 26),
                                    open=Decimal("10.50"),
                                    close=Decimal("10.80"),
                                    high=Decimal("10.90"),
                                    low=Decimal("10.40"),
                                    vol=1200,
                                    amount=Decimal("1100000"),
                                ),
                            ],
                        )
                    ],
                )

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
        payload = app.registered["compute_short_trend"]("2026-05-26", ["000001"], 2, 20)

        self.assertEqual(payload["items"][0]["code"], "000001")
        self.assertEqual(len(payload["items"][0]["values"]), 2)

    def test_registered_multi_trend_tool_returns_expected_payload_for_codes(self):
        if importlib.util.find_spec("mcp") is None:
            self.skipTest("mcp package is not installed in the current test environment")
        from mcp_stock_server.server import create_mcp_server

        class FakeQueryService:
            def list_stock_codes(self):
                return []

        class FakeWriteService:
            def get_stock_daily_bars(self, time, codes, limit=120):
                from datetime import date
                from decimal import Decimal
                from mcp_stock_server.models.db_models import DailyBar, StockDailyBarsItem
                from mcp_stock_server.models.response_models import GetStockDailyBarsResponse

                return GetStockDailyBarsResponse(
                    time=time,
                    items=[
                        StockDailyBarsItem(
                            code="000001",
                            daily_bars=[
                                DailyBar(
                                    code="000001",
                                    trade_date=date(2026, 5, 23),
                                    open=Decimal("10.00"),
                                    close=Decimal("10.00"),
                                    high=Decimal("10.10"),
                                    low=Decimal("9.90"),
                                    vol=1000,
                                    amount=Decimal("1000000"),
                                ),
                                DailyBar(
                                    code="000001",
                                    trade_date=date(2026, 5, 24),
                                    open=Decimal("10.00"),
                                    close=Decimal("11.00"),
                                    high=Decimal("11.10"),
                                    low=Decimal("9.95"),
                                    vol=1000,
                                    amount=Decimal("1000000"),
                                ),
                                DailyBar(
                                    code="000001",
                                    trade_date=date(2026, 5, 25),
                                    open=Decimal("11.00"),
                                    close=Decimal("12.00"),
                                    high=Decimal("12.10"),
                                    low=Decimal("10.90"),
                                    vol=1000,
                                    amount=Decimal("1000000"),
                                ),
                            ],
                        )
                    ],
                )

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
        payload = app.registered["compute_multi_trend"]("2026-05-26", ["000001"], [2, 3], 20)

        self.assertEqual(payload["items"][0]["code"], "000001")
        self.assertEqual(payload["items"][0]["values"][0], None)

    def test_registered_kdj_tool_returns_expected_payload_for_codes(self):
        if importlib.util.find_spec("mcp") is None:
            self.skipTest("mcp package is not installed in the current test environment")
        from mcp_stock_server.server import create_mcp_server

        class FakeQueryService:
            def list_stock_codes(self):
                return []

        class FakeWriteService:
            def get_stock_daily_bars(self, time, codes, limit=120):
                from datetime import date
                from decimal import Decimal
                from mcp_stock_server.models.db_models import DailyBar, StockDailyBarsItem
                from mcp_stock_server.models.response_models import GetStockDailyBarsResponse

                return GetStockDailyBarsResponse(
                    time=time,
                    items=[
                        StockDailyBarsItem(
                            code="000001",
                            daily_bars=[
                                DailyBar(
                                    code="000001",
                                    trade_date=date(2026, 5, 23),
                                    open=Decimal("9.00"),
                                    close=Decimal("9.10"),
                                    high=Decimal("10.00"),
                                    low=Decimal("8.00"),
                                    vol=1000,
                                    amount=Decimal("1000000"),
                                ),
                                DailyBar(
                                    code="000001",
                                    trade_date=date(2026, 5, 24),
                                    open=Decimal("9.10"),
                                    close=Decimal("10.00"),
                                    high=Decimal("11.00"),
                                    low=Decimal("8.50"),
                                    vol=1000,
                                    amount=Decimal("1000000"),
                                ),
                            ],
                        )
                    ],
                )

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
        payload = app.registered["compute_kdj"]("2026-05-26", ["000001"], 2, 3, 3, 20)

        self.assertEqual(payload["items"][0]["code"], "000001")
        self.assertEqual(len(payload["items"][0]["k"]), 2)

    def test_registered_amplitude_tool_returns_expected_payload(self):
        if importlib.util.find_spec("mcp") is None:
            self.skipTest("mcp package is not installed in the current test environment")
        from mcp_stock_server.server import create_mcp_server

        class FakeQueryService:
            def list_stock_codes(self):
                return []

        class FakeWriteService:
            def get_stock_daily_bars(self, time, codes, limit=120):
                from datetime import date
                from decimal import Decimal
                from mcp_stock_server.models.db_models import DailyBar, StockDailyBarsItem
                from mcp_stock_server.models.response_models import GetStockDailyBarsResponse

                return GetStockDailyBarsResponse(
                    time=time,
                    items=[
                        StockDailyBarsItem(
                            code="000001",
                            daily_bars=[
                                DailyBar(
                                    code="000001",
                                    trade_date=date(2026, 5, 25),
                                    open=Decimal("10.00"),
                                    close=Decimal("10.00"),
                                    high=Decimal("10.20"),
                                    low=Decimal("9.90"),
                                    vol=1000,
                                    amount=Decimal("1000000"),
                                ),
                                DailyBar(
                                    code="000001",
                                    trade_date=date(2026, 5, 26),
                                    open=Decimal("10.10"),
                                    close=Decimal("10.20"),
                                    high=Decimal("10.60"),
                                    low=Decimal("9.80"),
                                    vol=1000,
                                    amount=Decimal("1000000"),
                                ),
                            ],
                        )
                    ],
                )

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
        payload = app.registered["compute_amplitude"]("2026-05-26", ["000001"], 20)

        self.assertEqual(payload["items"][0]["code"], "000001")
        self.assertAlmostEqual(payload["items"][0]["value"], (10.60 - 9.80) / 10.00 * 100)

    def test_after_close_tool_accepts_only_time_argument(self):
        if importlib.util.find_spec("mcp") is None:
            self.skipTest("mcp package is not installed in the current test environment")
        from mcp_stock_server.server import create_mcp_server

        class FakeQueryService:
            def list_stock_codes(self):
                return []

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

        with self.assertRaises(TypeError):
            app.registered["insert_stock_daily_bars_after_close"](
                "2026-05-26",
                [],
            )

    def test_get_technical_snapshot_tool_returns_snapshot_and_bars(self):
        from mcp_stock_server.models.db_models import DailyBar, StockDailyBarsItem
        from mcp_stock_server.models.response_models import GetStockDailyBarsResponse
        from mcp_stock_server.tools.stock_tools import get_technical_snapshots_tool

        class FakeStockDailyService:
            def get_stock_daily_bars(self, time, codes, limit=120):
                self.last_call = (time, codes, limit)
                bars = []
                base_close = Decimal("10.00")
                for index in range(60):
                    close = base_close + Decimal(index) * Decimal("0.10")
                    bars.append(
                        DailyBar(
                            code="600000",
                            trade_date=date(2026, 3, 30) + timedelta(days=index),
                            open=close - Decimal("0.05"),
                            close=close,
                            high=close + Decimal("0.30"),
                            low=close - Decimal("0.20"),
                            vol=1000 + index * 10,
                            amount=Decimal(1000000 + index * 10000),
                        )
                    )
                return GetStockDailyBarsResponse(
                    time=time,
                    items=[StockDailyBarsItem(code="600000", daily_bars=bars)],
                )

        payload = get_technical_snapshots_tool(
            FakeStockDailyService(),
            {
                "symbols": ["600000"],
                "trade_date": "2026-05-28",
                "lookback_days": 60,
                "include_bars": True,
            },
        )

        self.assertEqual(payload["trade_date"], "2026-05-28")
        self.assertEqual(payload["items"][0]["symbol"], "600000")
        self.assertEqual(payload["items"][0]["bars_count"], 60)
        self.assertEqual(len(payload["items"][0]["bars"]), 60)
        self.assertEqual(payload["items"][0]["bars"][-1]["close"], "15.90")
        self.assertEqual(payload["items"][0]["technical_snapshot"]["data_sufficiency"], "ok")
        self.assertEqual(payload["items"][0]["technical_snapshot"]["macd_signal"], "bullish_above_zero")
        self.assertEqual(payload["items"][0]["technical_snapshot"]["rsi_state"], "overbought")
        self.assertEqual(
            payload["items"][0]["technical_snapshot"]["volume_price_pattern"],
            "volume_shrink_price_up",
        )
        for value in payload["items"][0]["technical_snapshot"].values():
            if isinstance(value, float):
                self.assertEqual(value, round(value, 4))

    def test_get_technical_snapshots_tool_returns_partial_failures(self):
        from mcp_stock_server.models.db_models import DailyBar, StockDailyBarsItem
        from mcp_stock_server.models.response_models import GetStockDailyBarsResponse
        from mcp_stock_server.tools.stock_tools import get_technical_snapshots_tool

        class FakeStockDailyService:
            def get_stock_daily_bars(self, time, codes, limit=120):
                ok_bars = []
                for index in range(25):
                    close = Decimal("20.00") + Decimal(index) * Decimal("0.20")
                    ok_bars.append(
                        DailyBar(
                            code="000001",
                            trade_date=date(2026, 5, 1) + timedelta(days=index),
                            open=close - Decimal("0.10"),
                            close=close,
                            high=close + Decimal("0.30"),
                            low=close - Decimal("0.20"),
                            vol=2000 + index * 20,
                            amount=Decimal(2000000 + index * 20000),
                        )
                    )
                insufficient_bars = ok_bars[:10]
                return GetStockDailyBarsResponse(
                    time=time,
                    items=[
                        StockDailyBarsItem(code="000001", daily_bars=ok_bars),
                        StockDailyBarsItem(code="300001", daily_bars=insufficient_bars),
                        StockDailyBarsItem(code="688001", daily_bars=[]),
                    ],
                )

        payload = get_technical_snapshots_tool(
            FakeStockDailyService(),
            {
                "symbols": ["000001", "300001", "688001"],
                "trade_date": "2026-05-25",
                "lookback_days": 60,
                "include_bars": False,
            },
        )

        self.assertEqual(payload["trade_date"], "2026-05-25")
        self.assertEqual([item["symbol"] for item in payload["items"]], ["000001", "300001"])
        self.assertNotIn("bars", payload["items"][0])
        self.assertEqual(payload["items"][1]["technical_snapshot"]["data_sufficiency"], "insufficient_history")
        self.assertEqual(
            payload["partial_failures"],
            [{"symbol": "688001", "reason": "insufficient_history"}],
        )

    def test_get_technical_snapshots_tool_builds_snapshots_concurrently(self):
        from mcp_stock_server.models.db_models import DailyBar, StockDailyBarsItem
        from mcp_stock_server.models.response_models import GetStockDailyBarsResponse
        from mcp_stock_server.services.technical_snapshot_service import TechnicalSnapshotService

        class FakeStockDailyService:
            def get_stock_daily_bars(self, time, codes, limit=120):
                items = []
                for code in codes:
                    bars = []
                    for index in range(25):
                        close = Decimal("10.00") + Decimal(index) * Decimal("0.10")
                        bars.append(
                            DailyBar(
                                code=code,
                                trade_date=date(2026, 5, 1) + timedelta(days=index),
                                open=close - Decimal("0.05"),
                                close=close,
                                high=close + Decimal("0.20"),
                                low=close - Decimal("0.20"),
                                vol=1000 + index * 10,
                                amount=Decimal(1000000 + index * 10000),
                            )
                        )
                    items.append(StockDailyBarsItem(code=code, daily_bars=bars))
                return GetStockDailyBarsResponse(time=time, items=items)

        service = TechnicalSnapshotService(FakeStockDailyService())
        symbols = ["000001", "000002", "000003"]
        started = threading.Barrier(len(symbols))
        active_threads: set[str] = set()
        active_threads_lock = threading.Lock()
        observed_parallelism = threading.Event()
        original_build_snapshot = TechnicalSnapshotService._build_snapshot

        def instrumented_build_snapshot(self, bars):
            with active_threads_lock:
                active_threads.add(threading.current_thread().name)
            if started.wait(timeout=1) == 0:
                observed_parallelism.set()
            return original_build_snapshot(self, bars)

        TechnicalSnapshotService._build_snapshot = instrumented_build_snapshot
        try:
            payload = service.get_technical_snapshots(
                symbols=symbols,
                trade_date=date(2026, 5, 25),
                lookback_days=60,
                include_bars=False,
            )
        finally:
            TechnicalSnapshotService._build_snapshot = original_build_snapshot

        self.assertTrue(observed_parallelism.is_set())
        self.assertGreater(len(active_threads), 1)
        self.assertEqual([item["symbol"] for item in payload["items"]], symbols)
        self.assertEqual(payload["partial_failures"], [])

    def test_registered_technical_snapshot_tools_return_expected_payloads(self):
        if importlib.util.find_spec("mcp") is None:
            self.skipTest("mcp package is not installed in the current test environment")
        from mcp_stock_server.server import create_mcp_server

        class FakeQueryService:
            def list_stock_codes(self):
                return []

        class FakeWriteService:
            def get_stock_daily_bars(self, time, codes, limit=120):
                bars_by_code = {}
                for code in codes:
                    bars = []
                    total = 60 if code == "600000" else 12
                    for index in range(total):
                        close = Decimal("10.00") + Decimal(index) * Decimal("0.10")
                        bars.append(
                            type(
                                "Bar",
                                (),
                                {
                                    "code": code,
                                    "trade_date": date(2026, 3, 30) + timedelta(days=index),
                                    "open": close - Decimal("0.05"),
                                    "close": close,
                                    "high": close + Decimal("0.30"),
                                    "low": close - Decimal("0.20"),
                                    "vol": 1000 + index * 10,
                                    "amount": Decimal(1000000 + index * 10000),
                                },
                            )()
                        )
                    bars_by_code[code] = bars
                return type(
                    "Response",
                    (),
                    {
                        "time": time,
                        "items": [
                            type("Item", (), {"code": code, "daily_bars": bars_by_code[code]})()
                            for code in codes
                        ],
                    },
                )()

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

        batch_payload = app.registered["get_technical_snapshot"](
            ["600000", "300001"],
            "2026-05-28",
            60,
            False,
        )

        self.assertIn("get_technical_snapshot", app.registered)
        self.assertEqual(batch_payload["items"][1]["technical_snapshot"]["data_sufficiency"], "insufficient_history")

    def test_run_streamable_http_server_uses_http_transport(self):
        if importlib.util.find_spec("mcp") is None:
            self.skipTest("mcp package is not installed in the current test environment")
        from mcp_stock_server.server import run_streamable_http_server

        @dataclass
        class FakeQueryService:
            pass

        @dataclass
        class FakeWriteService:
            pass

        class FakeFastMCP:
            def __init__(self, name):
                self.name = name
                self.registered = {}
                self.run_calls = []

            def tool(self, name=None, description=None):
                def decorator(func):
                    self.registered[name or func.__name__] = func
                    return func

                return decorator

            def run(self, transport="stdio"):
                self.run_calls.append(transport)

        app_holder = {}

        def fake_factory(name):
            app = FakeFastMCP(name)
            app_holder["app"] = app
            return app

        run_streamable_http_server(
            FakeQueryService(),
            FakeWriteService(),
            fastmcp_cls=fake_factory,
        )

        self.assertEqual(app_holder["app"].run_calls, ["streamable-http"])


if __name__ == "__main__":
    unittest.main()
