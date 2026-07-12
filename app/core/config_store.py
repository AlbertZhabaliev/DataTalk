from __future__ import annotations

import threading
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.config.settings import get_settings

_DEFAULT_PATH = get_settings().config_path


class ConfigStore:
    """Single source of truth for runtime config (LLM, DBs, glossary).

    Editable from the admin UI; persisted to app/data/app_config.yaml.
    Thread-safe so concurrent requests / saves are safe.
    """

    def __init__(self, path: str | Path = _DEFAULT_PATH) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"llm": {}, "databases": [], "glossary": {"databases": {}},
                    "defaults": {}}
        return yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                yaml.safe_dump(self._data, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )

    # ---- LLM ----
    def get_llm(self) -> dict[str, Any]:
        return self._data.setdefault("llm", {})

    def set_llm(self, value: dict[str, Any]) -> None:
        with self._lock:
            self._data["llm"] = value
        self.save()

    # ---- Databases ----
    def get_databases(self) -> list[dict[str, Any]]:
        return self._data.setdefault("databases", [])

    def set_databases(self, value: list[dict[str, Any]]) -> None:
        with self._lock:
            self._data["databases"] = value
        self.save()

    # ---- Glossary ----
    def get_glossary(self) -> dict[str, Any]:
        return self._data.setdefault("glossary", {"databases": {}}).get(
            "databases", {}
        )

    def set_glossary(self, value: dict[str, Any]) -> None:
        with self._lock:
            self._data.setdefault("glossary", {})["databases"] = value
        self.save()

    # ---- Defaults ----
    def get_defaults(self) -> dict[str, Any]:
        return self._data.setdefault("defaults", {})

    def get_default(self, key: str, default: Any = None) -> Any:
        return self.get_defaults().get(key, default)

    def set_default(self, key: str, value: Any) -> None:
        with self._lock:
            self.get_defaults()[key] = value
        self.save()


@lru_cache
def get_config_store() -> ConfigStore:
    return ConfigStore()
