"""Database engine and session helpers. SQLite locally, Postgres via DATABASE_URL."""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
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
    else:
        # Neon (and Render) drop idle connections. Recycle and ping so a
        # sleeping compute does not turn the next click into a 500.
        kwargs.update(pool_pre_ping=True, pool_recycle=280, pool_size=3, max_overflow=2)
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


def _column_names(inspector, table: str) -> set[str]:
    if table not in inspector.get_table_names():
        return set()
    return {col["name"] for col in inspector.get_columns(table)}


def _has_unique(inspector, table: str, columns: set[str]) -> bool:
    if table not in inspector.get_table_names():
        return False
    for item in inspector.get_unique_constraints(table):
        if set(item.get("column_names") or []) == columns:
            return True
    for item in inspector.get_indexes(table):
        if item.get("unique") and set(item.get("column_names") or []) == columns:
            return True
    return False


def migrate_schema(engine: Engine) -> None:
    """Add columns/tables as needed. Do not drop user data; sqlite rebuilds copy rows first."""
    inspector = inspect(engine)
    if "tournaments" not in inspector.get_table_names():
        return
    sqlite = engine.dialect.name == "sqlite"
    bool_true = "BOOLEAN DEFAULT 1" if sqlite else "BOOLEAN DEFAULT TRUE"
    bool_false = "BOOLEAN DEFAULT 0" if sqlite else "BOOLEAN DEFAULT FALSE"

    with engine.begin() as conn:
        if sqlite:
            conn.execute(text("PRAGMA foreign_keys=OFF"))

        existing = _column_names(inspect(conn) if sqlite else inspector, "tournaments")
        # Re-inspect inside the transaction for sqlite after possible earlier ALTERs.
        inspector = inspect(conn)
        existing = _column_names(inspector, "tournaments")
        additions = {
            "auto_assign_courts": bool_true,
            "day_start": "VARCHAR(8) DEFAULT '09:00'",
            "avg_match_minutes": "INTEGER DEFAULT 25",
            "changeover_minutes": "INTEGER DEFAULT 5",
            "break_every_waves": "INTEGER DEFAULT 3",
            "break_minutes": "INTEGER DEFAULT 15",
            "lunch_start": "VARCHAR(8)",
            "lunch_minutes": "INTEGER DEFAULT 45",
            "min_rest_minutes": "INTEGER DEFAULT 30",
        }
        for name, spec in additions.items():
            if name not in existing:
                conn.execute(text(f"ALTER TABLE tournaments ADD COLUMN {name} {spec}"))

        player_cols = _column_names(inspector, "players")
        if "event_id" not in player_cols:
            conn.execute(text("ALTER TABLE players ADD COLUMN event_id INTEGER"))

        draw_cols = _column_names(inspector, "draws")
        if "event_id" not in draw_cols:
            conn.execute(text("ALTER TABLE draws ADD COLUMN event_id INTEGER"))

        match_cols = _column_names(inspector, "matches")
        if "event_id" not in match_cols:
            conn.execute(text("ALTER TABLE matches ADD COLUMN event_id INTEGER"))
        if "expected_time" not in match_cols:
            conn.execute(text("ALTER TABLE matches ADD COLUMN expected_time VARCHAR(40)"))
        if "time_locked" not in match_cols:
            conn.execute(text(f"ALTER TABLE matches ADD COLUMN time_locked {bool_false}"))

        inspector = inspect(conn)
        if not sqlite:
            if _has_unique(inspector, "players", {"tournament_id", "seed"}):
                conn.execute(text("ALTER TABLE players DROP CONSTRAINT IF EXISTS uq_tournament_seed"))
            if _has_unique(inspector, "draws", {"tournament_id"}):
                conn.execute(text("ALTER TABLE draws DROP CONSTRAINT IF EXISTS draws_tournament_id_key"))

        if sqlite:
            inspector = inspect(conn)
            if _has_unique(inspector, "players", {"tournament_id", "seed"}):
                _sqlite_rebuild_players(conn)
            inspector = inspect(conn)
            if _has_unique(inspector, "draws", {"tournament_id"}):
                _sqlite_rebuild_draws(conn)
            conn.execute(text("PRAGMA foreign_keys=ON"))

    _backfill_default_events(engine)


def _sqlite_rebuild_players(conn) -> None:
    cols = [
        "id",
        "tournament_id",
        "event_id",
        "name",
        "club",
        "ranking",
        "seed",
        "player_code",
        "contact",
        "partner_name",
    ]
    inspector = inspect(conn)
    have = _column_names(inspector, "players")
    copy = [c for c in cols if c in have]
    listed = ", ".join(copy)
    conn.execute(text("ALTER TABLE players RENAME TO players_old"))
    conn.execute(
        text(
            """
            CREATE TABLE players (
                id INTEGER PRIMARY KEY,
                tournament_id INTEGER NOT NULL,
                event_id INTEGER,
                name VARCHAR(200) NOT NULL,
                club VARCHAR(200),
                ranking FLOAT,
                seed INTEGER,
                player_code VARCHAR(80),
                contact VARCHAR(200),
                partner_name VARCHAR(200),
                FOREIGN KEY(tournament_id) REFERENCES tournaments (id),
                FOREIGN KEY(event_id) REFERENCES events (id)
            )
            """
        )
    )
    conn.execute(text(f"INSERT INTO players ({listed}) SELECT {listed} FROM players_old"))
    conn.execute(text("DROP TABLE players_old"))


def _sqlite_rebuild_draws(conn) -> None:
    cols = [
        "id",
        "tournament_id",
        "event_id",
        "bracket_size",
        "byes",
        "generated_at",
        "locked",
        "rng_seed",
        "slots_json",
    ]
    inspector = inspect(conn)
    have = _column_names(inspector, "draws")
    copy = [c for c in cols if c in have]
    listed = ", ".join(copy)
    conn.execute(text("ALTER TABLE draws RENAME TO draws_old"))
    conn.execute(
        text(
            """
            CREATE TABLE draws (
                id INTEGER PRIMARY KEY,
                tournament_id INTEGER NOT NULL,
                event_id INTEGER,
                bracket_size INTEGER NOT NULL,
                byes INTEGER DEFAULT 0,
                generated_at DATETIME,
                locked BOOLEAN DEFAULT 0,
                rng_seed INTEGER,
                slots_json JSON,
                FOREIGN KEY(tournament_id) REFERENCES tournaments (id),
                FOREIGN KEY(event_id) REFERENCES events (id)
            )
            """
        )
    )
    conn.execute(text(f"INSERT INTO draws ({listed}) SELECT {listed} FROM draws_old"))
    conn.execute(text("DROP TABLE draws_old"))


def _backfill_default_events(engine: Engine) -> None:
    from models import Draw, EntryPerson, Event, Match, Person, Player, Tournament
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    session = Session(engine)
    try:
        rows = session.execute(
            select(Tournament).options(
                selectinload(Tournament.events),
                selectinload(Tournament.players).selectinload(Player.people_links),
                selectinload(Tournament.draws),
                selectinload(Tournament.matches),
            )
        ).scalars().all()
        for t in rows:
            if t.events:
                continue
            event = Event(
                tournament_id=t.id,
                name="Main Draw",
                event_type=t.event_type or "singles",
                format=t.format or "single_elimination",
                sort_order=0,
                best_of=t.best_of,
                points_per_game=t.points_per_game,
                third_game_points=t.third_game_points,
                deuce_enabled=t.deuce_enabled,
                win_by=t.win_by,
                max_score=t.max_score,
                status=t.status or "draft",
            )
            session.add(event)
            session.flush()
            for player in t.players:
                player.event_id = event.id
                if player.people_links:
                    continue
                person = Person(
                    tournament_id=t.id,
                    name=player.name.split(" / ")[0].strip() if player.name else "Player",
                    club=player.club,
                    ranking=player.ranking,
                    player_code=player.player_code,
                    contact=player.contact,
                )
                session.add(person)
                session.flush()
                session.add(EntryPerson(entry_id=player.id, person_id=person.id, slot=1))
                if player.partner_name:
                    partner = Person(
                        tournament_id=t.id,
                        name=player.partner_name,
                        club=player.club,
                    )
                    session.add(partner)
                    session.flush()
                    session.add(EntryPerson(entry_id=player.id, person_id=partner.id, slot=2))
            for draw in t.draws:
                draw.event_id = event.id
            # Older DBs stored a single draw via tournament_id only.
            if not t.draws:
                draw = session.execute(select(Draw).where(Draw.tournament_id == t.id)).scalar_one_or_none()
                if draw:
                    draw.event_id = event.id
            for match in t.matches:
                match.event_id = event.id
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Session:
    if SessionLocal is None:
        init_engine()
    assert SessionLocal is not None
    return SessionLocal()
