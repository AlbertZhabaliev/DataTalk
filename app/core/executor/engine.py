from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, URL

from app.config.settings import DEFAULT_PORTS, DbConfig


def _mssql_odbc(cfg: DbConfig) -> str:
    """Build an ODBC connection string, handling named instances + Win auth."""
    host = (cfg.host or "").strip()
    # Named instance (e.g. MYPC\SQLEXPRESS): no port — SQL Browser resolves it.
    server = host if "\\" in host else f"{host},{cfg.port or 1433}"
    parts = [
        "DRIVER={ODBC Driver 17 for SQL Server}",
        f"SERVER={server}",
    ]
    if cfg.database:
        parts.append(f"DATABASE={cfg.database}")
    if cfg.windows_auth:
        parts.append("Trusted_Connection=yes")
    else:
        parts.append(f"UID={cfg.username}")
        parts.append(f"PWD={cfg.password}")
    if cfg.trust_server_certificate:
        parts.append("TrustServerCertificate=yes")
    parts.append("Encrypt=no")
    parts.append(f"Connection Timeout={cfg.query_timeout_seconds}")
    return ";".join(parts) + ";"


def build_url(cfg: DbConfig) -> URL:
    port = cfg.port or DEFAULT_PORTS.get(cfg.engine, 0)
    if cfg.engine == "postgres":
        return URL.create(
            "postgresql+psycopg",
            username=cfg.username or None,
            password=cfg.password or None,
            host=cfg.host,
            port=port,
            database=cfg.database or None,
        )
    if cfg.engine == "mssql":
        return URL.create("mssql+pyodbc", query={"odbc_connect": _mssql_odbc(cfg)})
    if cfg.engine == "oracle":
        return URL.create(
            "oracle+oracledb",
            username=cfg.username or None,
            password=cfg.password or None,
            host=cfg.host,
            port=port,
            query={"service_name": cfg.database} if cfg.database else {},
        )
    if cfg.engine == "clickhouse":
        return URL.create(
            "clickhouse+native",
            username=cfg.username or "default",
            password=cfg.password or None,
            host=cfg.host,
            port=port,
            database=cfg.database or "default",
        )
    raise ValueError(f"Unsupported engine: {cfg.engine}")


def build_engine(cfg: DbConfig) -> Engine:
    url = build_url(cfg)
    connect_args: dict = {}
    if cfg.engine == "postgres":
        connect_args["connect_timeout"] = cfg.query_timeout_seconds
    return create_engine(url, pool_pre_ping=True, connect_args=connect_args)
