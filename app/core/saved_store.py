from __future__ import annotations

import threading
import time
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.config.settings import get_settings


def _default_path() -> Path:
    cfg = Path(get_settings().config_path)
    return cfg.parent / "saved_queries.yaml"


class SavedStore:
    """Persists user-saved queries (+ their chart config) to YAML."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else _default_path()
        self._lock = threading.Lock()
        self._data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"queries": []}
        return yaml.safe_load(self.path.read_text(encoding="utf-8")) or {"queries": []}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            yaml.safe_dump(self._data, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )

    def list(self) -> list[dict[str, Any]]:
        return self._data.setdefault("queries", [])

    def get(self, qid: str) -> dict[str, Any] | None:
        return next((q for q in self.list() if q.get("id") == qid), None)

    def upsert(self, item: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            queries = self.list()
            qid = item.get("id")
            if qid:
                for i, q in enumerate(queries):
                    if q.get("id") == qid:
                        queries[i] = {**q, **item, "updated_at": time.time()}
                        self._save()
                        return queries[i]
            item["id"] = uuid.uuid4().hex
            item["created_at"] = time.time()
            item["updated_at"] = item["created_at"]
            queries.insert(0, item)
            self._save()
            return item

    def delete(self, qid: str) -> bool:
        with self._lock:
            queries = self.list()
            n = len(queries)
            self._data["queries"] = [q for q in queries if q.get("id") != qid]
            if len(self._data["queries"]) != n:
                self._save()
                return True
            return False


@lru_cache
def get_saved_store() -> SavedStore:
    return SavedStore()
