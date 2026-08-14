"""Database engine and session helpers. SQLite locally, Postgres via DATABASE_URL."""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from models import Base

ENGINE: Engine | None = None
SessionLocal: sessionmaker[Session] | None = None


def default_sqlite_url() -> str:
    root = Path(__file__).resolve().parent
    data = root / "data"
    data.mkdir(parents=True, exist_ok=True)
    return "sqlite:///" + str(data / "tournament.db")


def normalize_url(url: str) -> str:
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://") :]
    return url


def get_database_url() -> str:
    return normalize_url(os.environ.get("DATABASE_URL") or default_sqlite_url())


def init_engine(url: str | None = None) -> Engine:
    global ENGINE, SessionLocal
    url = normalize_url(url or get_database_url())
    kwargs: dict = {"future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    ENGINE = create_engine(url, **kwargs)

    if url.startswith("sqlite"):

        @event.listens_for(ENGINE, "connect")
        def _sqlite_pragma(dbapi_conn, _connection_record):  # type: ignore[no-untyped-def]
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    SessionLocal = sessionmaker(bind=ENGINE, autoflush=False, autocommit=False, future=True)
    Base.metadata.create_all(ENGINE)
    return ENGINE


def get_session() -> Session:
    if SessionLocal is None:
        init_engine()
    assert SessionLocal is not None
    return SessionLocal()
