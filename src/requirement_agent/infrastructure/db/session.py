"""SQLAlchemy 引擎与会话管理。

统一提供：
- `engine`：单例数据库引擎（`pool_pre_ping` 防止连接被池复用后失效）。
- `SessionLocal`：会话工厂，仓库层通过它开/关事务（`SessionLocal() as session` 模式）。
- `get_db_session`：FastAPI 依赖注入用的请求级会话（自动关闭）。
- `check_database_connection`：健康检查用的探针。

时区约定：所有会话固定 `timezone=UTC`，保证 `NOW()` / `DEFAULT NOW()` 一律以 **UTC 落库**，
与业务展示时区（`settings.display_timezone`）解耦；对外显示由 `requirement_agent.common.time.as_display_iso` 负责。
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

from requirement_agent.config.settings import settings


engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    future=True,
    connect_args={"options": "-c timezone=UTC"},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()


def get_db_session() -> Generator:
    """FastAPI 依赖：提供一个请求作用域的 SQLAlchemy 会话，请求结束自动关闭。"""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def check_database_connection() -> tuple[bool, str]:
    """探测数据库连通性：能执行 SELECT 1 即视为健康，返回 (ok, 描述)。"""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, "database connection ok"
    except Exception as exc:  # pragma: no cover - depends on external service
        return False, str(exc)


__all__ = ["Base", "SessionLocal", "engine", "get_db_session", "check_database_connection"]
