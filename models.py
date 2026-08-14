"""SQLAlchemy models for Dolphin Tournament."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Tournament(Base):
    __tablename__ = "tournaments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    date: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    venue: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    organizer: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    logo_data: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sport: Mapped[str] = mapped_column(String(40), default="badminton")
    format: Mapped[str] = mapped_column(String(40), default="single_elimination")
    event_type: Mapped[str] = mapped_column(String(20), default="singles")
    expected_players: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    best_of: Mapped[int] = mapped_column(Integer, default=3)
    points_per_game: Mapped[int] = mapped_column(Integer, default=21)
    third_game_points: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    deuce_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    win_by: Mapped[int] = mapped_column(Integer, default=2)
    max_score: Mapped[Optional[int]] = mapped_column(Integer, default=30)
    num_courts: Mapped[int] = mapped_column(Integer, default=4)
    auto_assign_courts: Mapped[bool] = mapped_column(Boolean, default=True)
    day_start: Mapped[Optional[str]] = mapped_column(String(8), default="09:00")
    avg_match_minutes: Mapped[int] = mapped_column(Integer, default=25)
    changeover_minutes: Mapped[int] = mapped_column(Integer, default=5)
    break_every_waves: Mapped[int] = mapped_column(Integer, default=3)
    break_minutes: Mapped[int] = mapped_column(Integer, default=15)
    lunch_start: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    lunch_minutes: Mapped[int] = mapped_column(Integer, default=45)
    group_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    knockout_spots: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    players: Mapped[list[Player]] = relationship(
        back_populates="tournament", cascade="all, delete-orphan"
    )
    courts: Mapped[list[Court]] = relationship(
        back_populates="tournament", cascade="all, delete-orphan"
    )
    draw: Mapped[Optional[Draw]] = relationship(
        back_populates="tournament", cascade="all, delete-orphan", uselist=False
    )
    matches: Mapped[list[Match]] = relationship(
        back_populates="tournament", cascade="all, delete-orphan"
    )

    def scoring_rules(self) -> dict[str, Any]:
        return {
            "best_of": self.best_of,
            "points_per_game": self.points_per_game,
            "third_game_points": self.third_game_points,
            "deuce_enabled": self.deuce_enabled,
            "win_by": self.win_by,
            "max_score": self.max_score,
        }


class Player(Base):
    __tablename__ = "players"
    __table_args__ = (UniqueConstraint("tournament_id", "seed", name="uq_tournament_seed"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tournament_id: Mapped[int] = mapped_column(ForeignKey("tournaments.id"))
    name: Mapped[str] = mapped_column(String(200))
    club: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    ranking: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    seed: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    player_code: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    contact: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    partner_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    tournament: Mapped[Tournament] = relationship(back_populates="players")


class Draw(Base):
    __tablename__ = "draws"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tournament_id: Mapped[int] = mapped_column(ForeignKey("tournaments.id"), unique=True)
    bracket_size: Mapped[int] = mapped_column(Integer)
    byes: Mapped[int] = mapped_column(Integer, default=0)
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    rng_seed: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    slots_json: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)

    tournament: Mapped[Tournament] = relationship(back_populates="draw")


class Court(Base):
    __tablename__ = "courts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tournament_id: Mapped[int] = mapped_column(ForeignKey("tournaments.id"))
    name: Mapped[str] = mapped_column(String(80))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    tournament: Mapped[Tournament] = relationship(back_populates="courts")


class Match(Base):
    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tournament_id: Mapped[int] = mapped_column(ForeignKey("tournaments.id"))
    draw_id: Mapped[Optional[int]] = mapped_column(ForeignKey("draws.id"), nullable=True)
    round_index: Mapped[int] = mapped_column(Integer, default=0)
    round_name: Mapped[str] = mapped_column(String(40), default="")
    match_number: Mapped[int] = mapped_column(Integer)
    position_in_round: Mapped[int] = mapped_column(Integer, default=0)
    player1_id: Mapped[Optional[int]] = mapped_column(ForeignKey("players.id"), nullable=True)
    player2_id: Mapped[Optional[int]] = mapped_column(ForeignKey("players.id"), nullable=True)
    player1_is_bye: Mapped[bool] = mapped_column(Boolean, default=False)
    player2_is_bye: Mapped[bool] = mapped_column(Boolean, default=False)
    player1_source: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    player2_source: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    court_id: Mapped[Optional[int]] = mapped_column(ForeignKey("courts.id"), nullable=True)
    scheduled_time: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="not_started")
    scores: Mapped[Optional[Any]] = mapped_column(JSON, default=list)
    winner_id: Mapped[Optional[int]] = mapped_column(ForeignKey("players.id"), nullable=True)
    loser_id: Mapped[Optional[int]] = mapped_column(ForeignKey("players.id"), nullable=True)
    result_type: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    next_match_id: Mapped[Optional[int]] = mapped_column(ForeignKey("matches.id"), nullable=True)
    next_slot: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    tournament: Mapped[Tournament] = relationship(back_populates="matches")
    player1: Mapped[Optional[Player]] = relationship(foreign_keys=[player1_id])
    player2: Mapped[Optional[Player]] = relationship(foreign_keys=[player2_id])
    winner: Mapped[Optional[Player]] = relationship(foreign_keys=[winner_id])
    loser: Mapped[Optional[Player]] = relationship(foreign_keys=[loser_id])
    court: Mapped[Optional[Court]] = relationship()
    next_match: Mapped[Optional[Match]] = relationship(
        remote_side="Match.id", foreign_keys=[next_match_id]
    )
