from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class MySQLConfig:
    host: str
    port: int
    user: str
    password: str
    database: str

    @classmethod
    def from_file(cls, path: str | Path) -> "MySQLConfig":
        config_path = Path(path)
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        mysql = payload["mysql"]
        return cls(
            host=mysql["host"],
            port=int(mysql["port"]),
            user=mysql["user"],
            password=mysql["password"],
            database=mysql["database"],
        )

    @classmethod
    def from_env(cls) -> "MySQLConfig":
        return cls(
            host=os.getenv("MYSQL_HOST", "127.0.0.1"),
            port=int(os.getenv("MYSQL_PORT", "3306")),
            user=os.getenv("MYSQL_USER", "root"),
            password=os.getenv("MYSQL_PASSWORD", ""),
            database=os.getenv("MYSQL_DATABASE", "stocks"),
        )


def create_pymysql_connection(config: MySQLConfig) -> Any:
    try:
        import pymysql
    except ImportError as exc:
        raise RuntimeError(
            "pymysql is required for real MySQL connections. Install it before using MySQL repositories."
        ) from exc

    return pymysql.connect(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        database=config.database,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )
