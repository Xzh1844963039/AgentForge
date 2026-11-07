"""Append-only JSONL event log for execution observability."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class EventLogger:
    """Thread-safe JSONL lifecycle event writer.

    Events are a complement to SQLite: they are cheap, append-only, and useful
    for following a live workflow or debugging failures.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._file = self.path.open("a", encoding="utf-8", buffering=1)

    def event(self, name: str, **payload: Any) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": name,
            **payload,
        }
        line = json.dumps(record, ensure_ascii=False, default=str)
        with self._lock:
            self._file.write(line + "\n")
            self._file.flush()

    def close(self) -> None:
        with self._lock:
            if not self._file.closed:
                self._file.close()
