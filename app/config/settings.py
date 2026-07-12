from __future__ import annotations

from functools import lru_cache

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_PORTS = {
    "clickhouse": 9000,
    "postgres": 5432,
    "mssql": 1433,
    "oracle": 1521,
}


class DbConfig(BaseModel):
    name: str
    engine: str
    host: str
    port: int = 0
    database: str = ""
    username: str = ""
    password: str = ""
    # MS SQL only: use Windows / integrated authentication (Trusted_Connection).
    windows_auth: bool = False
    trust_server_certificate: bool = True
    read_only: bool = True
    max_rows: int = 1000
    query_timeout_seconds: int = 10

    def model_post_init(self, __context) -> None:
        if not self.port:
            self.port = DEFAULT_PORTS.get(self.engine, 0)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="NL2DB_", extra="ignore"
    )

    # Path to the single, frontend-editable config file.
    config_path: str = "app/data/app_config.yaml"

    # Fallback only; runtime values come from the config store.
    app_name: str = "NL2DB"
    default_max_rows: int = 1000
    default_query_timeout_seconds: int = 10


@lru_cache
def get_settings() -> Settings:
    return Settings()
