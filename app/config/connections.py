from __future__ import annotations

from app.config.settings import DbConfig
from app.core.config_store import get_config_store
from app.core.executor.base import SqlExecutor


class ConnectionRegistry:
    """Lazily holds one read-only executor per configured database.

    Rebuilds when the config store changes (admin UI edit).
    """

    def __init__(self) -> None:
        self._executors: dict[str, SqlExecutor] = {}
        self._configs: dict[str, DbConfig] = {}
        self.reload()

    def reload(self) -> None:
        self._executors.clear()
        self._configs = {
            c.name: c for c in self._load_configs()
        }

    def _load_configs(self) -> list[DbConfig]:
        out: list[DbConfig] = []
        for item in get_config_store().get_databases():
            try:
                out.append(DbConfig(**item))
            except Exception as e:  # skip invalid entries
                print(f"[connections] skip {item.get('name')}: {e}")
        return out

    def get(self, db_name: str) -> SqlExecutor:
        if db_name not in self._configs:
            raise KeyError(f"Unknown database: {db_name}")
        if db_name not in self._executors:
            self._executors[db_name] = SqlExecutor(self._configs[db_name])
        return self._executors[db_name]

    def list_names(self) -> list[str]:
        return list(self._configs.keys())


registry = ConnectionRegistry()
