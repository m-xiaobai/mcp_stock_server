from __future__ import annotations

import json
from pathlib import Path

from .models import AuditEntry


class JsonlAuditWriter:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, entry: AuditEntry) -> None:
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
