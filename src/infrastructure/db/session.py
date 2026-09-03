from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import psycopg
from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

from src.config.settings import settings


def ensure_database_exists() -> None:
    preferred_db = settings.postgres_db
    if settings.postgres_db in {"postgres", "template1", "template0"}:
        preferred_db = "requirement_agent"
        settings.postgres_db = preferred_db

    admin_dsn = (
        f"postgresql://{settings.postgres_user}:{settings.postgres_password}@"
        f"{settings.postgres_host}:{settings.postgres_port}/postgres"
    )
    with psycopg.connect(admin_dsn) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s",
                (preferred_db,),
            )
            exists = cur.fetchone() is not None
            if not exists:
                cur.execute(f'CREATE DATABASE "{preferred_db}"')

    schema_path = Path(__file__).resolve().parents[2] / "migrations" / "001_init_business_schema.sql"
    if not schema_path.exists():
        return

    with psycopg.connect(settings.psycopg_dsn) as conn:
        schema_sql = schema_path.read_text(encoding="utf-8")
        with conn.cursor() as cur:
            cur.execute(schema_sql)
        conn.commit()


ensure_database_exists()
engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()


def get_db_session() -> Generator:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def check_database_connection() -> tuple[bool, str]:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, "database connection ok"
    except Exception as exc:  # pragma: no cover - depends on external service
        return False, str(exc)


__all__ = ["Base", "SessionLocal", "engine", "get_db_session", "check_database_connection"]
