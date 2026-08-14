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


def _log_backend(url: str) -> None:
    if url.startswith("sqlite"):
        if os.environ.get("RENDER"):
            print(
                "[dolphin] database: sqlite — Render disk is ephemeral; "
                "set DATABASE_URL to Neon Postgres or tournament data will vanish on every deploy.",
                flush=True,
            )
        else:
            print("[dolphin] database: sqlite (data/tournament.db)", flush=True)
    else:
        print("[dolphin] database: postgres", flush=True)


def init_engine(url: str | None = None) -> Engine:
    global ENGINE, SessionLocal
    url = normalize_url(url or get_database_url())
    _log_backend(url)
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
    migrate_schema(ENGINE)
    return ENGINE


def migrate_schema(engine: Engine) -> None:
    """Add new columns to existing databases. Additive only: never drop tables or columns."""
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    if "tournaments" not in inspector.get_table_names():
        return
    existing = {col["name"] for col in inspector.get_columns("tournaments")}
    sqlite = engine.dialect.name == "sqlite"
    bool_true = "BOOLEAN DEFAULT 1" if sqlite else "BOOLEAN DEFAULT TRUE"
    additions = {
        "auto_assign_courts": bool_true,
        "day_start": "VARCHAR(8) DEFAULT '09:00'",
        "avg_match_minutes": "INTEGER DEFAULT 25",
        "changeover_minutes": "INTEGER DEFAULT 5",
        "break_every_waves": "INTEGER DEFAULT 3",
        "break_minutes": "INTEGER DEFAULT 15",
        "lunch_start": "VARCHAR(8)",
        "lunch_minutes": "INTEGER DEFAULT 45",
    }
    with engine.begin() as conn:
        for name, spec in additions.items():
            if name in existing:
                continue
            conn.execute(text(f"ALTER TABLE tournaments ADD COLUMN {name} {spec}"))


def get_session() -> Session:
    if SessionLocal is None:
        init_engine()
    assert SessionLocal is not None
    return SessionLocal()
