"""Tournament lifecycle: create, players, draw, matches, results."""

from __future__ import annotations

import csv
import io
import random
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from engine.bracket import apply_opening_byes, generate_matches, opponents_conflict
from engine.round_robin import generate_round_robin
from engine.schedule import assign_available_courts, estimate_times, normalize_hhmm, parse_hhmm
from engine.scoring import ScoreError, validate_score
from engine.seeding import DrawError, build_draw, generate_bracket_size, max_seeds_for_entries
from models import Court, Draw, EntryPerson, Event, Match, Person, Player, Tournament

FORMATS = {
    "single_elimination": "Single Elimination / Knockout",
    "round_robin": "Round Robin",
    "group_knockout": "Group Stage → Knockout",
}
SPORTS = ["badminton", "tennis", "table tennis", "squash", "pickleball"]
STATUSES = {"draft", "upcoming", "live", "completed"}
MATCH_STATUSES = {
    "not_started",
    "ready",
    "live",
    "completed",
    "walkover",
    "retired",
    "cancelled",
}


class AppError(ValueError):
    pass


class ConflictError(AppError):
    def __init__(self, message: str, conflicts: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.conflicts = conflicts or []


EVENT_PRESETS = {
    "WS": ("Women's Singles", "singles"),
    "WD": ("Women's Doubles", "doubles"),
    "XD": ("Mixed Doubles", "doubles"),
    "MS": ("Men's Singles", "singles"),
    "MD": ("Men's Doubles", "doubles"),
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def scoring_rules(t: Tournament) -> dict[str, Any]:
    return t.scoring_rules()


def default_event(t: Tournament) -> Event | None:
    events = sorted(t.events or [], key=lambda e: (e.sort_order, e.id or 0))
    return events[0] if events else None


def resolve_event(t: Tournament, event_id: Any = None) -> Event:
    if event_id not in (None, "", 0, "0"):
        try:
            eid = int(event_id)
        except (TypeError, ValueError) as exc:
            raise AppError("Unknown event.") from exc
        event = next((e for e in (t.events or []) if e.id == eid), None)
        if not event:
            raise AppError("Event not found.")
        return event
    event = default_event(t)
    if not event:
        raise AppError("This tournament has no events yet.")
    return event


def event_players(t: Tournament, event: Event | None) -> list[Player]:
    if event is None:
        return list(t.players or [])
    return [p for p in (t.players or []) if p.event_id == event.id]


def event_matches(t: Tournament, event: Event | None) -> list[Match]:
    if event is None:
        return list(t.matches or [])
    return [m for m in (t.matches or []) if m.event_id == event.id]


def event_draw(t: Tournament, event: Event | None) -> Draw | None:
    if event is None:
        return t.draw
    if event.draw:
        return event.draw
    return next((d for d in (t.draws or []) if d.event_id == event.id), None)


def ensure_default_event(session: Session, t: Tournament) -> Event:
    event = default_event(t)
    if event:
        return event
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
    if event not in t.events:
        t.events.append(event)
    return event


def _person_public(person: Person | None) -> dict[str, Any] | None:
    if person is None:
        return None
    return {
        "id": person.id,
        "name": person.name,
        "club": person.club or "",
        "ranking": person.ranking,
        "player_code": person.player_code or "",
        "contact": person.contact or "",
    }


def _entry_people(entry: Player | None) -> list[Person]:
    if entry is None:
        return []
    links = sorted(entry.people_links or [], key=lambda lnk: lnk.slot)
    return [lnk.person for lnk in links if lnk.person]


def entry_person_ids(entry: Player | None) -> list[int]:
    return [p.id for p in _entry_people(entry)]


def match_person_ids(match: Match) -> list[int]:
    ids: list[int] = []
    for entry in (match.player1, match.player2):
        people = entry_person_ids(entry)
        if people:
            ids.extend(people)
        elif entry and entry.id:
            ids.append(entry.id)
    return ids


def event_to_dict(event: Event) -> dict[str, Any]:
    return {
        "id": event.id,
        "name": event.name,
        "category": event.category or "",
        "event_type": event.event_type,
        "format": event.format,
        "format_label": FORMATS.get(event.format, event.format),
        "sort_order": event.sort_order,
        "status": event.status,
        "player_count": len(event.players or []),
        "locked": bool(event.draw and event.draw.locked),
        "generated": bool(event.draw),
        "scoring": event.scoring_rules(),
        "best_of": event.best_of,
        "points_per_game": event.points_per_game,
        "third_game_points": event.third_game_points,
        "deuce_enabled": event.deuce_enabled,
        "win_by": event.win_by,
        "max_score": event.max_score,
    }


def find_or_create_person(session: Session, t: Tournament, data: dict[str, Any]) -> Person:
    name = (data.get("name") or "").strip()
    if not name:
        raise AppError("Player name is required.")
    club = (data.get("club") or "").strip()
    for person in t.people or []:
        if person.name.strip().lower() == name.lower() and (person.club or "").strip().lower() == club.lower():
            if data.get("ranking") not in (None, ""):
                person.ranking = _opt_float(data.get("ranking"))
            if data.get("player_code"):
                person.player_code = str(data.get("player_code") or "").strip() or person.player_code
            if data.get("contact"):
                person.contact = str(data.get("contact") or "").strip() or person.contact
            return person
    person = Person(
        tournament_id=t.id,
        name=name,
        club=club or None,
        ranking=_opt_float(data.get("ranking")),
        player_code=(data.get("player_code") or "").strip() or None,
        contact=(data.get("contact") or "").strip() or None,
    )
    session.add(person)
    if person not in (t.people or []):
        t.people.append(person)
    session.flush()
    return person


def _player_public(p: Player | None) -> dict[str, Any] | None:
    if p is None:
        return None
    people = [_person_public(person) for person in _entry_people(p)]
    people = [row for row in people if row]
    return {
        "id": p.id,
        "name": p.name,
        "club": p.club or "",
        "ranking": p.ranking,
        "seed": p.seed,
        "player_code": p.player_code or "",
        "contact": p.contact or "",
        "partner_name": p.partner_name or "",
        "event_id": p.event_id,
        "people": people,
        "person_id": people[0]["id"] if len(people) == 1 else None,
    }


def tournament_to_dict(
    t: Tournament,
    include_nested: bool = False,
    event: Event | None = None,
) -> dict[str, Any]:
    event = event or default_event(t)
    players = event_players(t, event)
    draw = event_draw(t, event)
    data = {
        "id": t.id,
        "name": t.name,
        "date": t.date,
        "venue": t.venue,
        "organizer": t.organizer,
        "description": t.description,
        "logo_data": t.logo_data,
        "sport": t.sport,
        "format": (event.format if event else t.format),
        "format_label": FORMATS.get((event.format if event else t.format), t.format),
        "event_type": (event.event_type if event else t.event_type),
        "expected_players": t.expected_players,
        "best_of": (event.best_of if event else t.best_of),
        "points_per_game": (event.points_per_game if event else t.points_per_game),
        "third_game_points": (event.third_game_points if event else t.third_game_points),
        "deuce_enabled": (event.deuce_enabled if event else t.deuce_enabled),
        "win_by": (event.win_by if event else t.win_by),
        "max_score": (event.max_score if event else t.max_score),
        "num_courts": t.num_courts,
        "auto_assign_courts": bool(getattr(t, "auto_assign_courts", True)),
        "day_start": getattr(t, "day_start", None) or "09:00",
        "avg_match_minutes": getattr(t, "avg_match_minutes", None) or 25,
        "changeover_minutes": getattr(t, "changeover_minutes", None) or 5,
        "break_every_waves": getattr(t, "break_every_waves", None) or 0,
        "break_minutes": getattr(t, "break_minutes", None) or 15,
        "lunch_start": getattr(t, "lunch_start", None) or "",
        "lunch_minutes": getattr(t, "lunch_minutes", None) or 45,
        "min_rest_minutes": getattr(t, "min_rest_minutes", None) or 30,
        "group_count": t.group_count,
        "knockout_spots": t.knockout_spots,
        "status": t.status,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "player_count": len(players),
        "seed_count": sum(1 for p in players if p.seed),
        "scoring": event.scoring_rules() if event else t.scoring_rules(),
        "events": [event_to_dict(e) for e in sorted(t.events or [], key=lambda e: (e.sort_order, e.id or 0))],
        "event_id": event.id if event else None,
        "people": [_person_public(p) for p in sorted(t.people or [], key=lambda x: (x.name or "").lower())],
    }
    if include_nested:
        data["players"] = [_player_public(p) for p in sorted(players, key=_player_sort)]
        data["courts"] = [
            {"id": c.id, "name": c.name, "sort_order": c.sort_order}
            for c in sorted(t.courts, key=lambda c: c.sort_order)
        ]
        data["draw"] = None
        if draw:
            data["draw"] = {
                "id": draw.id,
                "bracket_size": draw.bracket_size,
                "byes": draw.byes,
                "generated_at": draw.generated_at.isoformat() if draw.generated_at else None,
                "locked": draw.locked,
            }
    return data


def _player_sort(p: Player) -> tuple:
    return (p.seed is None, p.seed or 0, (p.name or "").lower())


def load_tournament(session: Session, tid: int) -> Tournament:
    t = session.execute(
        select(Tournament)
        .execution_options(populate_existing=True)
        .options(
            selectinload(Tournament.events).selectinload(Event.draw),
            selectinload(Tournament.events).selectinload(Event.players),
            selectinload(Tournament.people).selectinload(Person.entry_links),
            selectinload(Tournament.players).selectinload(Player.people_links).selectinload(EntryPerson.person),
            selectinload(Tournament.players).selectinload(Player.event),
            selectinload(Tournament.courts),
            selectinload(Tournament.draws),
            selectinload(Tournament.matches).selectinload(Match.player1).selectinload(Player.people_links).selectinload(EntryPerson.person),
            selectinload(Tournament.matches).selectinload(Match.player2).selectinload(Player.people_links).selectinload(EntryPerson.person),
            selectinload(Tournament.matches).selectinload(Match.winner),
            selectinload(Tournament.matches).selectinload(Match.loser),
            selectinload(Tournament.matches).selectinload(Match.court),
            selectinload(Tournament.matches).selectinload(Match.event),
        )
        .where(Tournament.id == tid)
    ).scalar_one_or_none()
    if not t:
        raise AppError("Tournament not found.")
    if not t.events:
        ensure_default_event(session, t)
        session.flush()
        return load_tournament(session, tid)
    return t


def list_tournaments(session: Session) -> list[dict[str, Any]]:
    rows = session.execute(
        select(Tournament).options(selectinload(Tournament.players), selectinload(Tournament.events)).order_by(Tournament.id.desc())
    ).scalars().all()
    return [tournament_to_dict(t) for t in rows]


def create_tournament(session: Session, data: dict[str, Any]) -> Tournament:
    name = (data.get("name") or "").strip()
    if not name:
        raise AppError("Tournament name is required.")
    t = Tournament(
        name=name,
        date=(data.get("date") or "").strip() or None,
        venue=(data.get("venue") or "").strip() or None,
        organizer=(data.get("organizer") or "").strip() or None,
        description=(data.get("description") or "").strip() or None,
        logo_data=data.get("logo_data") or None,
        sport=(data.get("sport") or "badminton").strip().lower(),
        status="draft",
    )
    session.add(t)
    session.flush()
    _sync_courts(session, t, t.num_courts)
    ensure_default_event(session, t)
    return t


def update_tournament(session: Session, t: Tournament, data: dict[str, Any]) -> Tournament:
    simple = [
        "name",
        "date",
        "venue",
        "organizer",
        "description",
        "logo_data",
        "sport",
        "event_type",
    ]
    for key in simple:
        if key in data:
            val = data[key]
            if isinstance(val, str):
                val = val.strip() or None
            setattr(t, key, val if key != "name" else (val or t.name))
    if "format" in data:
        fmt = data["format"]
        if fmt not in FORMATS:
            raise AppError("Unknown tournament format.")
        if fmt == "group_knockout":
            raise AppError(
                "Group Stage → Knockout is prepared for a later version. "
                "Please use Knockout or Round Robin."
            )
        if t.draw and t.draw.locked:
            raise AppError("Unlock the draw before changing the tournament format.")
        t.format = fmt
        if len(t.events or []) == 1 and default_event(t):
            default_event(t).format = fmt
    ints = {
        "expected_players": (2, 256),
        "best_of": (1, 5),
        "points_per_game": (1, 99),
        "third_game_points": (1, 99),
        "win_by": (1, 10),
        "max_score": (1, 99),
        "num_courts": (1, 32),
        "avg_match_minutes": (5, 180),
        "changeover_minutes": (0, 30),
        "break_every_waves": (0, 20),
        "break_minutes": (0, 120),
        "lunch_minutes": (15, 180),
        "min_rest_minutes": (0, 180),
        "group_count": (2, 16),
        "knockout_spots": (1, 8),
    }
    for key, (lo, hi) in ints.items():
        if key not in data:
            continue
        raw = data[key]
        if raw in (None, "", False):
            if key in {"third_game_points", "max_score", "group_count", "knockout_spots", "expected_players"}:
                setattr(t, key, None)
            continue
        try:
            num = int(raw)
        except (TypeError, ValueError) as exc:
            raise AppError(f"Invalid {key.replace('_', ' ')}.") from exc
        if num < lo or num > hi:
            raise AppError(f"{key.replace('_', ' ')} must be between {lo} and {hi}.")
        if key == "best_of" and num not in {1, 3, 5}:
            raise AppError("Best of must be 1, 3, or 5.")
        setattr(t, key, num)
    if "deuce_enabled" in data:
        t.deuce_enabled = bool(data["deuce_enabled"])
    if "auto_assign_courts" in data:
        t.auto_assign_courts = bool(data["auto_assign_courts"])
    if "day_start" in data:
        t.day_start = normalize_hhmm(str(data.get("day_start") or ""), "09:00") or "09:00"
    if "lunch_start" in data:
        t.lunch_start = normalize_hhmm(str(data.get("lunch_start") or ""), None)
    if "num_courts" in data and t.num_courts:
        _sync_courts(session, t, t.num_courts)
    if any(k in data for k in ("format", "event_type", "best_of", "points_per_game", "third_game_points", "deuce_enabled", "win_by", "max_score")):
        ev = default_event(t)
        if ev and len(t.events or []) == 1:
            for key in ("event_type", "best_of", "points_per_game", "third_game_points", "deuce_enabled", "win_by", "max_score"):
                if key in data and getattr(t, key, None) is not None:
                    setattr(ev, key, getattr(t, key))
    if t.matches:
        refresh_courts_and_schedule(session, t)
    return t


def _sync_courts(session: Session, t: Tournament, count: int) -> None:
    existing = sorted(t.courts, key=lambda c: c.sort_order)
    while len(existing) < count:
        n = len(existing) + 1
        court = Court(tournament_id=t.id, name=f"Court {n}", sort_order=n)
        session.add(court)
        existing.append(court)
        if court not in t.courts:
            t.courts.append(court)
    session.flush()
    existing = sorted(t.courts, key=lambda c: c.sort_order)
    if len(existing) > count:
        live_ids = {
            m.court_id
            for m in t.matches
            if m.court_id and m.status in {"live", "ready"}
        }
        extra = existing[count:]
        for court in extra:
            if court.id in live_ids:
                raise AppError(f"{court.name} is in use and cannot be removed.")
            session.delete(court)
        t.courts[:] = existing[:count]


def _match_engine_view(m: Match) -> dict[str, Any]:
    return {
        "id": m.id,
        "round_index": m.round_index,
        "match_number": m.match_number,
        "player1_id": m.player1_id,
        "player2_id": m.player2_id,
        "player1_is_bye": m.player1_is_bye,
        "player2_is_bye": m.player2_is_bye,
        "player1_source": m.player1_source,
        "player2_source": m.player2_source,
        "court_id": m.court_id,
        "scheduled_time": m.scheduled_time,
        "expected_time": getattr(m, "expected_time", None) or m.scheduled_time,
        "time_locked": bool(getattr(m, "time_locked", False)),
        "status": m.status,
        "result_type": m.result_type,
        "winner_id": m.winner_id,
        "person_ids": match_person_ids(m),
        "event_id": m.event_id,
    }


def schedule_settings(t: Tournament) -> dict[str, Any]:
    return {
        "day_start": getattr(t, "day_start", None) or "09:00",
        "avg_match_minutes": getattr(t, "avg_match_minutes", None) or 25,
        "changeover_minutes": getattr(t, "changeover_minutes", None) or 5,
        "break_every_waves": getattr(t, "break_every_waves", None) or 0,
        "break_minutes": getattr(t, "break_minutes", None) or 15,
        "lunch_start": getattr(t, "lunch_start", None) or "",
        "lunch_minutes": getattr(t, "lunch_minutes", None) or 45,
        "min_rest_minutes": getattr(t, "min_rest_minutes", None) or 30,
    }


def refresh_courts_and_schedule(
    session: Session,
    t: Tournament,
    assign: bool | None = None,
    times: bool = True,
) -> None:
    """Fill courts (if auto-assign is on) and estimated start times."""
    if not t.matches:
        return
    if t.num_courts:
        _sync_courts(session, t, t.num_courts)
    courts = sorted(t.courts, key=lambda c: c.sort_order)
    views = [_match_engine_view(m) for m in t.matches]
    should_assign = bool(getattr(t, "auto_assign_courts", True)) if assign is None else assign
    if should_assign and courts:
        assign_available_courts(views, [c.id for c in courts])
        by_id = {row["id"]: row for row in views}
        for match in t.matches:
            if match.status in {"completed", "walkover", "retired", "cancelled"}:
                continue
            match.court_id = by_id[match.id].get("court_id")
            session.expire(match, ["court"])
    if times:
        estimate_times(views, len(courts) or t.num_courts or 1, schedule_settings(t))
        by_id = {row["id"]: row for row in views}
        for match in t.matches:
            if match.result_type == "bye":
                continue
            if match.status in {"completed", "walkover", "retired"} and match.scheduled_time:
                continue
            if getattr(match, "time_locked", False):
                continue
            row = by_id[match.id]
            match.scheduled_time = row.get("scheduled_time")
            match.expected_time = row.get("expected_time") or row.get("scheduled_time")
    session.flush()


def add_player(session: Session, t: Tournament, data: dict[str, Any]) -> Player:
    event = resolve_event(t, data.get("event_id"))
    _ensure_unlocked(t, event)
    person = None
    if data.get("person_id"):
        person = session.get(Person, int(data["person_id"]))
        if not person or person.tournament_id != t.id:
            raise AppError("Person not found.")
    else:
        person = find_or_create_person(session, t, data)
    players = event_players(t, event)
    for other in players:
        if person.id in entry_person_ids(other):
            raise AppError(f"{person.name} is already in {event.name}.")
    partner = None
    partner_name = (data.get("partner_name") or "").strip()
    if data.get("partner_id"):
        partner = session.get(Person, int(data["partner_id"]))
        if not partner or partner.tournament_id != t.id:
            raise AppError("Partner not found.")
    elif partner_name or event.event_type == "doubles":
        if partner_name:
            partner = find_or_create_person(
                session,
                t,
                {
                    "name": partner_name,
                    "club": data.get("partner_club") or data.get("club") or person.club or "",
                },
            )
            if partner.id == person.id:
                raise AppError("A doubles entry needs two different people.")
            for other in players:
                if partner.id in entry_person_ids(other):
                    raise AppError(f"{partner.name} is already in {event.name}.")
    display = f"{person.name} / {partner.name}" if partner else person.name
    club = (data.get("club") or person.club or "").strip()
    for other in players:
        if other.name.strip().lower() == display.lower() and (other.club or "").strip().lower() == club.lower():
            raise AppError(f"{display} is already registered" + (f" for {club}." if club else "."))
    player = Player(
        tournament_id=t.id,
        event_id=event.id,
        name=display,
        club=club or None,
        ranking=_opt_float(data.get("ranking")) if data.get("ranking") not in (None, "") else person.ranking,
        seed=_opt_seed(data.get("seed"), len(players) + 1),
        player_code=(data.get("player_code") or person.player_code or "").strip() or None,
        contact=(data.get("contact") or person.contact or "").strip() or None,
        partner_name=partner.name if partner else None,
    )
    _assert_unique_seed(t, player.seed, None, event)
    session.add(player)
    t.players.append(player)
    if event.players is not None and player not in event.players:
        event.players.append(player)
    session.flush()
    session.add(EntryPerson(entry_id=player.id, person_id=person.id, slot=1))
    if partner:
        session.add(EntryPerson(entry_id=player.id, person_id=partner.id, slot=2))
    session.flush()
    return player


def add_players_bulk(session: Session, t: Tournament, names: list[str], event_id: Any = None) -> list[Player]:
    created = []
    for raw in names:
        line = (raw or "").strip()
        if not line:
            continue
        club = ""
        name = line
        if "," in line:
            name, club = [x.strip() for x in line.split(",", 1)]
        created.append(add_player(session, t, {"name": name, "club": club, "event_id": event_id}))
    if not created:
        raise AppError("No players to add.")
    return created


def import_players_csv(session: Session, t: Tournament, text: str, event_id: Any = None) -> list[Player]:
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise AppError("CSV needs a header row (name, club, ranking, seed, ...).")
    created = []
    for row in reader:
        keys = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
        name = keys.get("name") or keys.get("player") or keys.get("player name")
        if not name:
            continue
        created.append(
            add_player(
                session,
                t,
                {
                    "name": name,
                    "club": keys.get("club") or keys.get("academy") or "",
                    "ranking": keys.get("ranking") or None,
                    "seed": keys.get("seed") or None,
                    "player_code": keys.get("player_id") or keys.get("player code") or keys.get("id") or "",
                    "contact": keys.get("contact") or keys.get("phone") or keys.get("email") or "",
                    "partner_name": keys.get("partner") or keys.get("partner name") or "",
                    "event_id": event_id,
                },
            )
        )
    if not created:
        raise AppError("No player rows found in the CSV.")
    return created


def update_player(session: Session, t: Tournament, player: Player, data: dict[str, Any]) -> Player:
    event = player.event or resolve_event(t, player.event_id)
    if event_draw(t, event) and event_draw(t, event).locked and any(k in data for k in ("name", "seed")):
        raise AppError("Unlock the draw before changing names or seeds.")
    if "name" in data:
        name = (data.get("name") or "").strip()
        if not name:
            raise AppError("Player name is required.")
        player.name = name
    for key in ("club", "player_code", "contact", "partner_name"):
        if key in data:
            val = (data.get(key) or "").strip() or None
            setattr(player, key, val)
    if "ranking" in data:
        player.ranking = _opt_float(data.get("ranking"))
    if "seed" in data:
        _ensure_unlocked(t, event)
        seed = _opt_seed(data.get("seed"), len(event_players(t, event)))
        _assert_unique_seed(t, seed, player.id, event)
        player.seed = seed
    return player


def delete_player(session: Session, t: Tournament, player: Player) -> None:
    event = player.event or resolve_event(t, player.event_id)
    _ensure_unlocked(t, event)
    if event_draw(t, event):
        raise AppError("Clear the draw before removing players.")
    session.delete(player)


def assign_seeds_by_ranking(session: Session, t: Tournament, event_id: Any = None) -> None:
    event = resolve_event(t, event_id)
    _ensure_unlocked(t, event)
    players = event_players(t, event)
    ranked = sorted(
        [p for p in players if p.ranking is not None],
        key=lambda p: p.ranking or 0,
    )
    if not ranked:
        raise AppError("Add ranking numbers before seeding by ranking.")
    fmt = event.format or t.format
    cap = max_seeds_for_entries(len(players)) if fmt != "round_robin" else len(ranked)
    for player in players:
        player.seed = None
    session.flush()
    for i, player in enumerate(ranked[:cap], start=1):
        player.seed = i


def _opt_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise AppError("Ranking must be a number.") from exc


def _opt_seed(value: Any, max_n: int) -> int | None:
    if value in (None, ""):
        return None
    try:
        seed = int(value)
    except (TypeError, ValueError) as exc:
        raise AppError("Seed must be a whole number.") from exc
    if seed < 1:
        raise AppError("Seed must be 1 or higher.")
    return seed


def _assert_unique_seed(
    t: Tournament, seed: int | None, exclude_id: int | None, event: Event | None = None
) -> None:
    if seed is None:
        return
    for other in event_players(t, event):
        if other.id != exclude_id and other.seed == seed:
            raise AppError(f"Seed {seed} is already assigned.")


def _ensure_unlocked(t: Tournament, event: Event | None = None) -> None:
    draw = event_draw(t, event) if event else t.draw
    if draw and draw.locked:
        raise AppError("The draw is locked. Unlock it before changing players or seeds.")


def draw_preview(t: Tournament, event: Event | None = None) -> dict[str, Any]:
    event = event or default_event(t)
    players = event_players(t, event)
    fmt = (event.format if event else t.format) or t.format
    n = len(players)
    size = generate_bracket_size(n) if n >= 2 and fmt != "round_robin" else n
    byes = (size - n) if fmt == "single_elimination" and n >= 2 else 0
    draw = event_draw(t, event)
    return {
        "tournament": t.name,
        "event": event.name if event else t.name,
        "format": FORMATS.get(fmt, fmt),
        "event_type": event.event_type if event else t.event_type,
        "players": n,
        "seeds": sum(1 for p in players if p.seed),
        "bracket_size": size if fmt == "single_elimination" else None,
        "byes": byes,
        "scoring": event.scoring_rules() if event else t.scoring_rules(),
        "sport": t.sport,
        "courts": t.num_courts,
        "locked": bool(draw and draw.locked),
        "generated": bool(draw),
    }


def generate_draw(
    session: Session,
    t: Tournament,
    rng_seed: int | None = None,
    confirm: bool = False,
    event_id: Any = None,
) -> Draw:
    event = resolve_event(t, event_id)
    fmt = event.format or t.format
    if fmt == "group_knockout":
        raise AppError("Group Stage → Knockout is not available yet.")
    draw = event_draw(t, event)
    if draw and draw.locked:
        raise AppError("Unlock the draw before generating a new one.")
    players = event_players(t, event)
    if len(players) < 2:
        raise AppError("Add at least 2 players before generating a draw.")
    matches = event_matches(t, event)
    if matches:
        has_result = any(m.winner_id for m in matches if m.result_type != "bye")
        if has_result and not confirm:
            raise AppError(
                "Changing the draw after matches have started may invalidate existing results."
            )
        for m in matches:
            m.next_match_id = None
        session.flush()
        for m in list(matches):
            session.delete(m)
        session.flush()
        t.matches[:] = [m for m in t.matches if m.event_id != event.id]
    if draw:
        session.delete(draw)
        session.flush()

    player_rows = [{"id": p.id, "name": p.name, "seed": p.seed} for p in players]
    rng_seed = rng_seed if rng_seed is not None else random.SystemRandom().randint(1, 1_000_000_000)
    rng = random.Random(rng_seed)

    if fmt == "round_robin":
        payload = generate_round_robin([p.id for p in players])
        draw = Draw(
            tournament_id=t.id,
            event_id=event.id,
            bracket_size=len(players),
            byes=0,
            locked=False,
            rng_seed=rng_seed,
            slots_json=[{"player_id": p.id} for p in players],
        )
        session.add(draw)
        session.flush()
        event.draw = draw
        _persist_matches(session, t, draw, payload, event)
        if t.status == "draft":
            t.status = "upcoming"
        event.status = "upcoming"
        session.flush()
        refresh_courts_and_schedule(session, t)
        return draw

    try:
        plan = build_draw(player_rows, rng)
    except DrawError as exc:
        raise AppError(str(exc)) from exc
    payload = generate_matches(plan["slots"])
    apply_opening_byes(payload)
    draw = Draw(
        tournament_id=t.id,
        event_id=event.id,
        bracket_size=plan["bracket_size"],
        byes=plan["byes"],
        locked=False,
        rng_seed=rng_seed,
        slots_json=plan["slots"],
    )
    session.add(draw)
    session.flush()
    event.draw = draw
    _persist_matches(session, t, draw, payload, event)
    if t.status == "draft":
        t.status = "upcoming"
    event.status = "upcoming"
    session.flush()
    refresh_courts_and_schedule(session, t)
    return draw


def _persist_matches(
    session: Session,
    t: Tournament,
    draw: Draw,
    payload: list[dict[str, Any]],
    event: Event | None = None,
) -> None:
    created: dict[int, Match] = {}
    for row in payload:
        match = Match(
            tournament_id=t.id,
            event_id=(event.id if event else draw.event_id),
            draw_id=draw.id,
            round_index=row["round_index"],
            round_name=row["round_name"],
            match_number=row["match_number"],
            position_in_round=row["position_in_round"],
            player1_id=row.get("player1_id"),
            player2_id=row.get("player2_id"),
            player1_is_bye=bool(row.get("player1_is_bye")),
            player2_is_bye=bool(row.get("player2_is_bye")),
            player1_source=row.get("player1_source"),
            player2_source=row.get("player2_source"),
            status=row.get("status") or "not_started",
            scores=row.get("scores") or [],
            winner_id=row.get("winner_id"),
            loser_id=row.get("loser_id"),
            result_type=row.get("result_type"),
            next_slot=row.get("next_slot"),
        )
        session.add(match)
        created[row["match_number"]] = match
        t.matches.append(match)
    session.flush()
    for row in payload:
        nxt_num = row.get("next_match_number")
        if nxt_num:
            created[row["match_number"]].next_match_id = created[nxt_num].id


def lock_draw(session: Session, t: Tournament, event_id: Any = None) -> Draw:
    event = resolve_event(t, event_id)
    draw = event_draw(t, event)
    if not draw:
        raise AppError("Generate a draw first.")
    draw.locked = True
    return draw


def unlock_draw(session: Session, t: Tournament, confirm: bool = False, event_id: Any = None) -> Draw:
    event = resolve_event(t, event_id)
    draw = event_draw(t, event)
    if not draw:
        raise AppError("No draw to unlock.")
    played = [m for m in event_matches(t, event) if m.winner_id and m.result_type != "bye"]
    if played and not confirm:
        raise AppError(
            "Changing the draw after matches have started may invalidate existing results."
        )
    draw.locked = False
    return draw


def start_tournament(session: Session, t: Tournament, event_id: Any = None) -> Tournament:
    event = resolve_event(t, event_id) if event_id else None
    targets = [event] if event else list(t.events or [])
    ready = [ev for ev in targets if event_draw(t, ev)]
    if not ready:
        raise AppError("Generate the draw before starting.")
    for ev in ready:
        draw = event_draw(t, ev)
        if draw and not draw.locked:
            draw.locked = True
        ev.status = "live"
    if t.status != "completed":
        t.status = "live"
    session.flush()
    refresh_courts_and_schedule(session, t)
    return t


def match_to_dict(m: Match) -> dict[str, Any]:
    court = m.court
    if court is None and m.court_id and m.tournament is not None:
        court = next((c for c in m.tournament.courts if c.id == m.court_id), None)
    locked = bool(getattr(m, "time_locked", False))
    expected = getattr(m, "expected_time", None) or m.scheduled_time
    return {
        "id": m.id,
        "event_id": m.event_id,
        "event_name": m.event.name if m.event else "",
        "round_index": m.round_index,
        "round_name": m.round_name,
        "match_number": m.match_number,
        "position_in_round": m.position_in_round,
        "player1": _player_public(m.player1),
        "player2": _player_public(m.player2),
        "player1_is_bye": m.player1_is_bye,
        "player2_is_bye": m.player2_is_bye,
        "player1_source": m.player1_source,
        "player2_source": m.player2_source,
        "court": {"id": court.id, "name": court.name} if court else None,
        "court_id": m.court_id,
        "scheduled_time": m.scheduled_time,
        "expected_time": expected,
        "time_locked": locked,
        "time_label": "CONFIRMED" if locked else ("EXPECTED" if expected else ""),
        "status": m.status,
        "scores": m.scores or [],
        "winner_id": m.winner_id,
        "loser_id": m.loser_id,
        "result_type": m.result_type,
        "next_match_id": m.next_match_id,
        "next_slot": m.next_slot,
        "person_ids": match_person_ids(m),
    }


def start_match(session: Session, t: Tournament, match: Match) -> Match:
    if t.status == "draft":
        raise AppError("Generate and start the tournament first.")
    event = match.event or default_event(t)
    draw = event_draw(t, event)
    if not draw:
        raise AppError("No draw.")
    if not draw.locked:
        raise AppError("Lock the draw before starting matches.")
    if match.status in {"completed", "walkover", "retired", "cancelled"}:
        raise AppError("This match is already finished.")
    if match.player1_is_bye or match.player2_is_bye:
        raise AppError("Bye matches advance automatically.")
    if not match.player1_id or not match.player2_id:
        raise AppError("Both players must be present to start this match.")
    live = [m for m in t.matches if m.status == "live"]
    live_people = set()
    for other in live:
        live_people.update(match_person_ids(other))
    if set(match_person_ids(match)) & live_people:
        raise AppError("A player is already in a live match.")
    packed = [
        {"id": m.id, "status": m.status, "player1_id": m.player1_id, "player2_id": m.player2_id}
        for m in live
    ]
    if opponents_conflict(packed, match.player1_id, match.id) or opponents_conflict(
        packed, match.player2_id, match.id
    ):
        raise AppError("A player is already in a live match.")
    if getattr(t, "auto_assign_courts", True) and t.courts:
        occupied = {
            m.court_id
            for m in t.matches
            if m.id != match.id and m.court_id and m.status in {"live", "ready"}
        }
        free = [c.id for c in sorted(t.courts, key=lambda c: c.sort_order) if c.id not in occupied]
        if match.court_id and match.court_id in occupied:
            raise AppError("That court already has a match. Finish it first.")
        if not match.court_id:
            if not free:
                raise AppError("All courts are busy. Enter a result to free one.")
            match.court_id = free[0]
    match.status = "live"
    match.started_at = utcnow()
    if t.status == "upcoming":
        t.status = "live"
    return match


def enter_result(
    session: Session,
    t: Tournament,
    match: Match,
    data: dict[str, Any],
) -> Match:
    event = match.event or default_event(t)
    draw = event_draw(t, event)
    if not draw:
        raise AppError("Generate a draw first.")
    if not draw.locked:
        raise AppError("Lock the draw before entering results.")
    if match.status in {"completed", "walkover", "retired", "cancelled"} and match.winner_id:
        raise AppError("This match is already completed.")
    if match.player1_is_bye or match.player2_is_bye:
        raise AppError("Bye matches cannot be scored.")
    if not match.player1_id or not match.player2_id:
        raise AppError("Both players must be present to enter a result.")

    result_type = (data.get("result_type") or "normal").lower()
    games = data.get("scores") or data.get("games") or []
    rules = event.scoring_rules() if event else t.scoring_rules()
    try:
        checked = validate_score(games, rules, result_type)
    except ScoreError as exc:
        raise AppError(str(exc)) from exc

    winner_id = data.get("winner_id")
    if result_type == "normal":
        side = checked["winner_side"]
        auto_id = match.player1_id if side == 1 else match.player2_id
        if winner_id and int(winner_id) != auto_id:
            raise AppError("Winner does not match the entered scores.")
        winner_id = auto_id
    else:
        if not winner_id:
            raise AppError("Select a winner.")
        winner_id = int(winner_id)
        if winner_id not in {match.player1_id, match.player2_id}:
            raise AppError("Winner must be one of the two players in this match.")

    loser_id = match.player2_id if winner_id == match.player1_id else match.player1_id
    match.scores = checked["games"]
    match.winner_id = winner_id
    match.loser_id = loser_id
    match.result_type = result_type
    match.completed_at = utcnow()
    status_map = {
        "normal": "completed",
        "walkover": "walkover",
        "retirement": "retired",
        "disqualification": "completed",
        "no_show": "walkover",
    }
    match.status = status_map.get(result_type, "completed")
    if t.status != "completed":
        t.status = "live"

    fmt = (event.format if event else t.format) or t.format
    if fmt == "single_elimination":
        _advance_persisted(session, t, match)

    remaining = [
        m
        for m in event_matches(t, event)
        if m.status not in {"completed", "walkover", "retired", "cancelled"}
        and not m.player1_is_bye
        and not m.player2_is_bye
    ]
    if fmt == "single_elimination":
        event_ms = event_matches(t, event)
        if event_ms:
            final = max(event_ms, key=lambda m: m.round_index)
            if final.winner_id:
                event.status = "completed"
                active = [ev for ev in t.events if event_matches(t, ev)]
                if active and all(ev.status == "completed" for ev in active):
                    t.status = "completed"
    elif fmt == "round_robin" and not remaining:
        event.status = "completed"
        active = [ev for ev in t.events if event_matches(t, ev)]
        if active and all(ev.status == "completed" for ev in active):
            t.status = "completed"
    session.flush()
    refresh_courts_and_schedule(session, t)
    return match


def _advance_persisted(session: Session, t: Tournament, match: Match) -> None:
    if not match.next_match_id or not match.winner_id:
        return
    nxt = session.get(Match, match.next_match_id)
    if not nxt:
        return
    if nxt.status in {"completed", "walkover", "retired"}:
        return
    if match.next_slot == 1:
        nxt.player1_id = match.winner_id
        nxt.player1_is_bye = False
        nxt.player1_source = None
    else:
        nxt.player2_id = match.winner_id
        nxt.player2_is_bye = False
        nxt.player2_source = None
    session.flush()
    session.refresh(nxt)
    if nxt.player1_id and nxt.player2_id:
        nxt.status = "ready"
    elif nxt.player1_id or nxt.player2_id:
        nxt.status = "not_started"


def assign_court(
    session: Session,
    t: Tournament,
    match: Match,
    court_id: int | None,
    scheduled_time: str | None = None,
    force: bool = False,
    time_locked: bool | None = None,
) -> tuple[Match, list[dict[str, Any]]]:
    if court_id:
        court = session.get(Court, int(court_id))
        if not court or court.tournament_id != t.id:
            raise AppError("Unknown court.")
        if match.status == "live":
            for other in t.matches:
                if other.id != match.id and other.court_id == court.id and other.status == "live":
                    raise AppError(f"{court.name} already has a live match.")
        if match.player1_id and match.player2_id and match.status in {"live", "ready"}:
            live = [m for m in t.matches if m.status == "live" and m.id != match.id]
            live_people = set()
            for other in live:
                live_people.update(match_person_ids(other))
            if set(match_person_ids(match)) & live_people:
                raise AppError("A player is already in a live match.")
        match.court_id = court.id
    else:
        match.court_id = None
    if scheduled_time is not None:
        stamp = scheduled_time.strip() or None
        match.scheduled_time = stamp
        match.expected_time = stamp
        if time_locked is not None:
            match.time_locked = bool(time_locked)
        elif stamp:
            match.time_locked = True
        else:
            match.time_locked = False
    warnings = person_schedule_conflicts(t, match)
    if warnings and not force:
        raise ConflictError(
            "Schedule conflict: " + "; ".join(w["message"] for w in warnings) + " Override to keep this time.",
            warnings,
        )
    return match, warnings


def results_payload(t: Tournament, event: Event | None = None) -> dict[str, Any]:
    event = event or default_event(t)
    matches = sorted(event_matches(t, event), key=lambda m: m.match_number)
    finished = [m for m in matches if m.winner_id and m.result_type != "bye"]
    champion = runner = None
    semis: list[dict[str, Any]] = []
    quarters: list[dict[str, Any]] = []
    fmt = (event.format if event else t.format) or t.format
    if fmt == "single_elimination" and matches:
        final = max(matches, key=lambda m: (m.round_index, m.match_number))
        if final.winner_id:
            champion = _player_public(final.winner)
            runner = _player_public(final.loser)
        for m in matches:
            if m.round_name == "Semifinals" and m.loser_id:
                semis.append(_player_public(m.loser))
            if m.round_name == "Quarterfinals" and m.loser_id:
                quarters.append(_player_public(m.loser))
        def uniq(items: list) -> list:
            seen = set()
            out = []
            for item in items:
                if not item or item["id"] in seen:
                    continue
                seen.add(item["id"])
                out.append(item)
            return out

        semis = uniq(semis)
        quarters = uniq(quarters)
    standings = _rr_standings(t, event) if fmt == "round_robin" else None
    return {
        "champion": champion,
        "runner_up": runner,
        "semifinalists": semis,
        "quarterfinalists": quarters,
        "matches": [match_to_dict(m) for m in finished],
        "standings": standings,
        "event_id": event.id if event else None,
        "event_name": event.name if event else t.name,
    }


def _rr_standings(t: Tournament, event: Event | None = None) -> list[dict[str, Any]]:
    table: dict[int, dict[str, Any]] = {}
    for p in event_players(t, event):
        table[p.id] = {
            "player": _player_public(p),
            "played": 0,
            "wins": 0,
            "losses": 0,
            "games_for": 0,
            "games_against": 0,
            "points_for": 0,
            "points_against": 0,
        }
    for m in event_matches(t, event):
        if not m.winner_id or not m.player1_id or not m.player2_id:
            continue
        table[m.player1_id]["played"] += 1
        table[m.player2_id]["played"] += 1
        table[m.winner_id]["wins"] += 1
        if m.loser_id:
            table[m.loser_id]["losses"] += 1
        for a, b in m.scores or []:
            table[m.player1_id]["points_for"] += a
            table[m.player1_id]["points_against"] += b
            table[m.player2_id]["points_for"] += b
            table[m.player2_id]["points_against"] += a
            if a > b:
                table[m.player1_id]["games_for"] += 1
                table[m.player2_id]["games_against"] += 1
            elif b > a:
                table[m.player2_id]["games_for"] += 1
                table[m.player1_id]["games_against"] += 1
    rows = list(table.values())
    rows.sort(
        key=lambda r: (
            -r["wins"],
            -(r["games_for"] - r["games_against"]),
            -(r["points_for"] - r["points_against"]),
        )
    )
    return rows


def player_page(t: Tournament, player: Player) -> dict[str, Any]:
    history = []
    wins = losses = 0
    current_round = None
    next_match = None
    for m in sorted(t.matches, key=lambda x: x.match_number):
        if player.id not in {m.player1_id, m.player2_id}:
            continue
        if m.result_type == "bye":
            current_round = m.round_name
            continue
        history.append(match_to_dict(m))
        if m.winner_id == player.id:
            wins += 1
            current_round = m.round_name
        elif m.loser_id == player.id:
            losses += 1
        elif m.status in {"ready", "live", "not_started"}:
            current_round = m.round_name
            if next_match is None:
                next_match = match_to_dict(m)
    status = "Registered"
    if t.status == "completed":
        final = max(t.matches, key=lambda m: m.round_index) if t.matches else None
        if final and final.winner_id == player.id:
            status = "Champion"
        elif final and final.loser_id == player.id:
            status = "Runner-up"
        elif losses:
            status = "Eliminated"
        else:
            status = "Completed"
    elif next_match:
        status = "Active"
    elif losses:
        status = "Eliminated"
    elif t.draw:
        status = "In draw"
    return {
        "player": _player_public(player),
        "matches_played": wins + losses,
        "wins": wins,
        "losses": losses,
        "current_status": status,
        "current_round": current_round,
        "next_match": next_match,
        "history": history,
    }


def person_page(t: Tournament, person: Person) -> dict[str, Any]:
    entry_ids = {lnk.entry_id for lnk in (person.entry_links or [])}
    entries = [p for p in t.players if p.id in entry_ids]
    history = []
    schedule = []
    wins = losses = 0
    next_match = None
    for m in sorted(t.matches, key=lambda x: (x.scheduled_time or "99", x.match_number)):
        if m.player1_id not in entry_ids and m.player2_id not in entry_ids:
            continue
        if m.result_type == "bye":
            continue
        row = match_to_dict(m)
        history.append(row)
        if m.status in {"ready", "live", "not_started"} and not m.player1_is_bye and not m.player2_is_bye:
            schedule.append(row)
            if next_match is None and m.status in {"ready", "live", "not_started"}:
                next_match = row
        if m.winner_id in entry_ids:
            wins += 1
        elif m.loser_id in entry_ids:
            losses += 1
    return {
        "person": _person_public(person),
        "entries": [
            {
                **_player_public(entry),
                "event_name": entry.event.name if entry.event else "",
                "event_id": entry.event_id,
            }
            for entry in entries
        ],
        "matches_played": wins + losses,
        "wins": wins,
        "losses": losses,
        "next_match": next_match,
        "schedule": schedule,
        "history": history,
    }


def full_payload(t: Tournament, event_id: Any = None) -> dict[str, Any]:
    event = resolve_event(t, event_id)
    scoped = event_matches(t, event)
    matches = [match_to_dict(m) for m in sorted(scoped, key=lambda m: m.match_number)]
    upcoming = [
        m
        for m in matches
        if m["status"] in {"ready", "not_started"}
        and not m["player1_is_bye"]
        and not m["player2_is_bye"]
        and m["player1"]
        and m["player2"]
    ]
    upcoming.sort(key=lambda m: (0 if m.get("court_id") else 1, m.get("scheduled_time") or "99", m["match_number"]))
    waiting = [m for m in upcoming if not m.get("court_id")]
    on_court = [m for m in upcoming if m.get("court_id")]
    live = [m for m in matches if m["status"] == "live"]
    all_playable = [
        match_to_dict(m)
        for m in sorted(t.matches, key=lambda x: (x.scheduled_time or "99", x.match_number))
        if not m.player1_is_bye and not m.player2_is_bye and m.result_type != "bye"
    ]
    return {
        "tournament": tournament_to_dict(t, include_nested=True, event=event),
        "event": event_to_dict(event),
        "matches": matches,
        "upcoming": on_court[:12],
        "waiting": waiting[:12],
        "live": live,
        "results": results_payload(t, event),
        "preview": draw_preview(t, event),
        "schedule": all_playable,
        "conflicts": all_person_conflicts(t),
    }


def create_event(session: Session, t: Tournament, data: dict[str, Any]) -> Event:
    category = (data.get("category") or "").strip().upper() or None
    preset = EVENT_PRESETS.get(category or "")
    name = (data.get("name") or "").strip()
    event_type = (data.get("event_type") or "").strip() or (preset[1] if preset else "singles")
    if not name:
        name = preset[0] if preset else "New Event"
    if any(e.name.strip().lower() == name.lower() for e in t.events):
        raise AppError(f"{name} already exists in this tournament.")
    fmt = data.get("format") or t.format or "single_elimination"
    if fmt not in FORMATS:
        raise AppError("Unknown event format.")
    if fmt == "group_knockout":
        raise AppError("Group Stage → Knockout is not available yet.")
    order = max((e.sort_order for e in t.events), default=-1) + 1
    event = Event(
        tournament_id=t.id,
        name=name,
        category=category,
        event_type=event_type if event_type in {"singles", "doubles"} else "singles",
        format=fmt,
        sort_order=int(data.get("sort_order") or order),
        best_of=int(data.get("best_of") or t.best_of or 3),
        points_per_game=int(data.get("points_per_game") or t.points_per_game or 21),
        third_game_points=t.third_game_points,
        deuce_enabled=t.deuce_enabled if data.get("deuce_enabled") is None else bool(data.get("deuce_enabled")),
        win_by=int(data.get("win_by") or t.win_by or 2),
        max_score=t.max_score if data.get("max_score") in (None, "") else int(data.get("max_score")),
        status="draft",
    )
    session.add(event)
    session.flush()
    t.events.append(event)
    return event


def update_event(session: Session, t: Tournament, event: Event, data: dict[str, Any]) -> Event:
    draw = event_draw(t, event)
    if "format" in data:
        fmt = data["format"]
        if fmt not in FORMATS:
            raise AppError("Unknown event format.")
        if fmt == "group_knockout":
            raise AppError("Group Stage → Knockout is not available yet.")
        if draw and draw.locked:
            raise AppError("Unlock the draw before changing the event format.")
        event.format = fmt
    if "name" in data:
        name = (data.get("name") or "").strip()
        if not name:
            raise AppError("Event name is required.")
        event.name = name
    if "category" in data:
        event.category = (data.get("category") or "").strip().upper() or None
    if "event_type" in data:
        event.event_type = data["event_type"] if data["event_type"] in {"singles", "doubles"} else event.event_type
    ints = {
        "best_of": (1, 5),
        "points_per_game": (1, 99),
        "third_game_points": (1, 99),
        "win_by": (1, 10),
        "max_score": (1, 99),
        "sort_order": (0, 99),
    }
    for key, (lo, hi) in ints.items():
        if key not in data:
            continue
        raw = data[key]
        if raw in (None, ""):
            if key in {"third_game_points", "max_score"}:
                setattr(event, key, None)
            continue
        num = int(raw)
        if num < lo or num > hi:
            raise AppError(f"{key.replace('_', ' ')} must be between {lo} and {hi}.")
        setattr(event, key, num)
    if "deuce_enabled" in data:
        event.deuce_enabled = bool(data["deuce_enabled"])
    return event


def delete_event(session: Session, t: Tournament, event: Event) -> None:
    if len(t.events or []) <= 1:
        raise AppError("A tournament needs at least one event.")
    if event_draw(t, event) and event_draw(t, event).locked:
        raise AppError("Unlock and clear this event’s draw before deleting it.")
    session.delete(event)


def _match_window(match: Match, duration: int) -> tuple[int, int] | None:
    start = parse_hhmm(match.scheduled_time, None)
    if start is None:
        return None
    return start, start + max(5, duration)


def _windows_conflict(a: tuple[int, int], b: tuple[int, int], min_rest: int) -> bool:
    a0, a1 = a
    b0, b1 = b
    if a0 < b1 and b0 < a1:
        return True
    gap = b0 - a1 if b0 >= a1 else a0 - b1
    return gap < min_rest


def person_schedule_conflicts(t: Tournament, match: Match) -> list[dict[str, Any]]:
    duration = int(getattr(t, "avg_match_minutes", None) or 25)
    min_rest = int(getattr(t, "min_rest_minutes", None) or 0)
    window = _match_window(match, duration)
    if not window:
        return []
    people = set(match_person_ids(match))
    if not people:
        return []
    names = {}
    for entry in (match.player1, match.player2):
        for person in _entry_people(entry):
            names[person.id] = person.name
    found: list[dict[str, Any]] = []
    for other in t.matches:
        if other.id == match.id:
            continue
        if other.result_type == "bye" or other.player1_is_bye or other.player2_is_bye:
            continue
        if other.status == "cancelled":
            continue
        shared = people & set(match_person_ids(other))
        if not shared:
            continue
        other_window = _match_window(other, duration)
        if not other_window:
            continue
        if not _windows_conflict(window, other_window, min_rest):
            continue
        overlap = window[0] < other_window[1] and other_window[0] < window[1]
        for pid in shared:
            who = names.get(pid) or f"Player {pid}"
            kind = "overlap" if overlap else "rest"
            message = (
                f"{who} is already in {other.event.name if other.event else 'another event'} "
                f"Match {other.match_number} at {other.scheduled_time}."
                if overlap
                else f"{who} needs {min_rest} minutes rest after {other.event.name if other.event else 'another'} Match {other.match_number} ({other.scheduled_time})."
            )
            found.append(
                {
                    "person_id": pid,
                    "person_name": who,
                    "kind": kind,
                    "other_match_id": other.id,
                    "other_match_number": other.match_number,
                    "other_event": other.event.name if other.event else "",
                    "other_time": other.scheduled_time,
                    "message": message,
                }
            )
    return found


def all_person_conflicts(t: Tournament) -> list[dict[str, Any]]:
    seen: set[tuple[int, int]] = set()
    out: list[dict[str, Any]] = []
    playable = [
        m
        for m in t.matches
        if m.scheduled_time and m.result_type != "bye" and not m.player1_is_bye and not m.player2_is_bye
    ]
    for match in playable:
        for row in person_schedule_conflicts(t, match):
            key = tuple(sorted((match.id, row["other_match_id"]))) + (row["person_id"],)
            if key in seen:
                continue
            seen.add(key)
            out.append({"match_id": match.id, "match_number": match.match_number, **row})
    return out
