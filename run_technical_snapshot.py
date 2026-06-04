from __future__ import annotations

import argparse
import json
from pathlib import Path
from decimal import Decimal

if __package__ in (None, ""):
    import sys

    CURRENT_DIR = Path(__file__).resolve().parent
    PROJECT_ROOT = CURRENT_DIR.parent
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

    from mcp_stock_server.main import build_mysql_services
    from mcp_stock_server.tools.stock_tools import get_technical_snapshots_tool
else:
    from .main import build_mysql_services
    from .tools.stock_tools import get_technical_snapshots_tool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the batch technical snapshot API directly without starting the MCP server."
    )
    parser.add_argument(
        "--symbols",
        nargs="+",
        required=True,
        help="One or more stock codes, e.g. 600000 000001 600519",
    )
    parser.add_argument(
        "--trade-date",
        required=True,
        help="Trade date in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=60,
        help="Number of recent bars to load. Default: 60.",
    )
    parser.add_argument(
        "--include-bars",
        action="store_true",
        help="Include raw daily bars in the output payload.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    _, stock_daily_service = build_mysql_services()
    payload = {
        "symbols": args.symbols,
        "trade_date": args.trade_date,
        "lookback_days": args.lookback_days,
        "include_bars": args.include_bars,
    }
    result = get_technical_snapshots_tool(stock_daily_service, payload)
    print(json.dumps(format_output_numbers(result), ensure_ascii=False, indent=2))


def format_output_numbers(value):
    if isinstance(value, dict):
        return {key: format_output_numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [format_output_numbers(item) for item in value]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return float(f"{value:.4f}")
    if isinstance(value, Decimal):
        return float(f"{value:.4f}")
    return value


if __name__ == "__main__":
    main()
