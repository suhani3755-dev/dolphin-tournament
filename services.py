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
from engine.scoring import ScoreError, validate_score
from engine.seeding import DrawError, build_draw, generate_bracket_size
from models import Court, Draw, Match, Player, Tournament

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


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def scoring_rules(t: Tournament) -> dict[str, Any]:
    return t.scoring_rules()


def _player_public(p: Player | None) -> dict[str, Any] | None:
    if p is None:
        return None
    return {
        "id": p.id,
        "name": p.name,
        "club": p.club or "",
        "ranking": p.ranking,
        "seed": p.seed,
        "player_code": p.player_code or "",
        "contact": p.contact or "",
        "partner_name": p.partner_name or "",
    }


def tournament_to_dict(t: Tournament, include_nested: bool = False) -> dict[str, Any]:
    data = {
        "id": t.id,
        "name": t.name,
        "date": t.date,
        "venue": t.venue,
        "organizer": t.organizer,
        "description": t.description,
        "logo_data": t.logo_data,
        "sport": t.sport,
        "format": t.format,
        "format_label": FORMATS.get(t.format, t.format),
        "event_type": t.event_type,
        "expected_players": t.expected_players,
        "best_of": t.best_of,
        "points_per_game": t.points_per_game,
        "third_game_points": t.third_game_points,
        "deuce_enabled": t.deuce_enabled,
        "win_by": t.win_by,
        "max_score": t.max_score,
        "num_courts": t.num_courts,
        "group_count": t.group_count,
        "knockout_spots": t.knockout_spots,
        "status": t.status,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "player_count": len(t.players) if t.players is not None else 0,
        "seed_count": sum(1 for p in (t.players or []) if p.seed),
        "scoring": t.scoring_rules(),
    }
    if include_nested:
        data["players"] = [_player_public(p) for p in sorted(t.players, key=_player_sort)]
        data["courts"] = [
            {"id": c.id, "name": c.name, "sort_order": c.sort_order}
            for c in sorted(t.courts, key=lambda c: c.sort_order)
        ]
        data["draw"] = None
        if t.draw:
            data["draw"] = {
                "id": t.draw.id,
                "bracket_size": t.draw.bracket_size,
                "byes": t.draw.byes,
                "generated_at": t.draw.generated_at.isoformat() if t.draw.generated_at else None,
                "locked": t.draw.locked,
            }
    return data


def _player_sort(p: Player) -> tuple:
    return (p.seed is None, p.seed or 0, (p.name or "").lower())


def load_tournament(session: Session, tid: int) -> Tournament:
    t = session.execute(
        select(Tournament)
        .options(
            selectinload(Tournament.players),
            selectinload(Tournament.courts),
            selectinload(Tournament.draw),
            selectinload(Tournament.matches).selectinload(Match.player1),
            selectinload(Tournament.matches).selectinload(Match.player2),
            selectinload(Tournament.matches).selectinload(Match.winner),
            selectinload(Tournament.matches).selectinload(Match.loser),
            selectinload(Tournament.matches).selectinload(Match.court),
        )
        .where(Tournament.id == tid)
    ).scalar_one_or_none()
    if not t:
        raise AppError("Tournament not found.")
    return t


def list_tournaments(session: Session) -> list[dict[str, Any]]:
    rows = session.execute(
        select(Tournament).options(selectinload(Tournament.players)).order_by(Tournament.id.desc())
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
    ints = {
        "expected_players": (2, 256),
        "best_of": (1, 5),
        "points_per_game": (1, 99),
        "third_game_points": (1, 99),
        "win_by": (1, 10),
        "max_score": (1, 99),
        "num_courts": (1, 32),
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
    if "num_courts" in data and t.num_courts:
        _sync_courts(session, t, t.num_courts)
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


def add_player(session: Session, t: Tournament, data: dict[str, Any]) -> Player:
    _ensure_unlocked(t)
    name = (data.get("name") or "").strip()
    if not name:
        raise AppError("Player name is required.")
    club = (data.get("club") or "").strip()
    for other in t.players:
        if other.name.strip().lower() == name.lower() and (other.club or "").strip().lower() == club.lower():
            raise AppError(f"{name} is already registered" + (f" for {club}." if club else "."))
    player = Player(
        tournament_id=t.id,
        name=name,
        club=club or None,
        ranking=_opt_float(data.get("ranking")),
        seed=_opt_seed(data.get("seed"), len(t.players) + 1),
        player_code=(data.get("player_code") or "").strip() or None,
        contact=(data.get("contact") or "").strip() or None,
        partner_name=(data.get("partner_name") or "").strip() or None,
    )
    _assert_unique_seed(t, player.seed, None)
    session.add(player)
    t.players.append(player)
    session.flush()
    return player


def add_players_bulk(session: Session, t: Tournament, names: list[str]) -> list[Player]:
    created = []
    for raw in names:
        line = (raw or "").strip()
        if not line:
            continue
        club = ""
        name = line
        if "," in line:
            name, club = [x.strip() for x in line.split(",", 1)]
        created.append(add_player(session, t, {"name": name, "club": club}))
    if not created:
        raise AppError("No players to add.")
    return created


def import_players_csv(session: Session, t: Tournament, text: str) -> list[Player]:
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
                },
            )
        )
    if not created:
        raise AppError("No player rows found in the CSV.")
    return created


def update_player(session: Session, t: Tournament, player: Player, data: dict[str, Any]) -> Player:
    if t.draw and t.draw.locked and any(k in data for k in ("name", "seed")):
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
        _ensure_unlocked(t)
        seed = _opt_seed(data.get("seed"), len(t.players))
        _assert_unique_seed(t, seed, player.id)
        player.seed = seed
    return player


def delete_player(session: Session, t: Tournament, player: Player) -> None:
    _ensure_unlocked(t)
    if t.draw:
        raise AppError("Clear the draw before removing players.")
    session.delete(player)


def assign_seeds_by_ranking(session: Session, t: Tournament) -> None:
    _ensure_unlocked(t)
    ranked = sorted(
        [p for p in t.players if p.ranking is not None],
        key=lambda p: p.ranking or 0,
    )
    if not ranked:
        raise AppError("Add ranking numbers before seeding by ranking.")
    for player in t.players:
        player.seed = None
    session.flush()
    for i, player in enumerate(ranked, start=1):
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


def _assert_unique_seed(t: Tournament, seed: int | None, exclude_id: int | None) -> None:
    if seed is None:
        return
    for other in t.players:
        if other.id != exclude_id and other.seed == seed:
            raise AppError(f"Seed {seed} is already assigned.")


def _ensure_unlocked(t: Tournament) -> None:
    if t.draw and t.draw.locked:
        raise AppError("The draw is locked. Unlock it before changing players or seeds.")


def draw_preview(t: Tournament) -> dict[str, Any]:
    n = len(t.players)
    size = generate_bracket_size(n) if n >= 2 and t.format != "round_robin" else n
    byes = (size - n) if t.format == "single_elimination" and n >= 2 else 0
    return {
        "tournament": t.name,
        "format": FORMATS.get(t.format, t.format),
        "event_type": t.event_type,
        "players": n,
        "seeds": sum(1 for p in t.players if p.seed),
        "bracket_size": size if t.format == "single_elimination" else None,
        "byes": byes,
        "scoring": t.scoring_rules(),
        "sport": t.sport,
        "courts": t.num_courts,
        "locked": bool(t.draw and t.draw.locked),
        "generated": bool(t.draw),
    }


def generate_draw(
    session: Session,
    t: Tournament,
    rng_seed: int | None = None,
    confirm: bool = False,
) -> Draw:
    if t.format == "group_knockout":
        raise AppError("Group Stage → Knockout is not available yet.")
    if t.draw and t.draw.locked:
        raise AppError("Unlock the draw before generating a new one.")
    if len(t.players) < 2:
        raise AppError("Add at least 2 players before generating a draw.")
    if t.matches:
        has_result = any(m.winner_id for m in t.matches if m.result_type != "bye")
        if has_result and not confirm:
            raise AppError(
                "Changing the draw after matches have started may invalidate existing results."
            )
        for m in t.matches:
            m.next_match_id = None
        session.flush()
        for m in list(t.matches):
            session.delete(m)
        t.matches.clear()
        session.flush()
    if t.draw:
        session.delete(t.draw)
        t.draw = None
        session.flush()

    players = [
        {"id": p.id, "name": p.name, "seed": p.seed}
        for p in t.players
    ]
    rng_seed = rng_seed if rng_seed is not None else random.SystemRandom().randint(1, 1_000_000_000)
    rng = random.Random(rng_seed)

    if t.format == "round_robin":
        payload = generate_round_robin([p.id for p in t.players])
        draw = Draw(
            tournament_id=t.id,
            bracket_size=len(t.players),
            byes=0,
            locked=False,
            rng_seed=rng_seed,
            slots_json=[{"player_id": p.id} for p in t.players],
        )
        session.add(draw)
        session.flush()
        t.draw = draw
        _persist_matches(session, t, draw, payload)
        t.status = "upcoming"
        return draw

    try:
        plan = build_draw(players, rng)
    except DrawError as exc:
        raise AppError(str(exc)) from exc
    payload = generate_matches(plan["slots"])
    apply_opening_byes(payload)
    draw = Draw(
        tournament_id=t.id,
        bracket_size=plan["bracket_size"],
        byes=plan["byes"],
        locked=False,
        rng_seed=rng_seed,
        slots_json=plan["slots"],
    )
    session.add(draw)
    session.flush()
    t.draw = draw
    _persist_matches(session, t, draw, payload)
    t.status = "upcoming"
    return draw


def _persist_matches(
    session: Session, t: Tournament, draw: Draw, payload: list[dict[str, Any]]
) -> None:
    created: dict[int, Match] = {}
    for row in payload:
        match = Match(
            tournament_id=t.id,
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


def lock_draw(session: Session, t: Tournament) -> Draw:
    if not t.draw:
        raise AppError("Generate a draw first.")
    t.draw.locked = True
    return t.draw


def unlock_draw(session: Session, t: Tournament, confirm: bool = False) -> Draw:
    if not t.draw:
        raise AppError("No draw to unlock.")
    played = [m for m in t.matches if m.winner_id and m.result_type != "bye"]
    if played and not confirm:
        raise AppError(
            "Changing the draw after matches have started may invalidate existing results."
        )
    t.draw.locked = False
    if t.status == "live" and played:
        pass
    return t.draw


def start_tournament(session: Session, t: Tournament) -> Tournament:
    if not t.draw:
        raise AppError("Generate the draw before starting.")
    if not t.draw.locked:
        t.draw.locked = True
    if t.status != "completed":
        t.status = "live"
    return t


def match_to_dict(m: Match) -> dict[str, Any]:
    return {
        "id": m.id,
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
        "court": {"id": m.court.id, "name": m.court.name} if m.court else None,
        "court_id": m.court_id,
        "scheduled_time": m.scheduled_time,
        "status": m.status,
        "scores": m.scores or [],
        "winner_id": m.winner_id,
        "loser_id": m.loser_id,
        "result_type": m.result_type,
        "next_match_id": m.next_match_id,
        "next_slot": m.next_slot,
    }


def start_match(session: Session, t: Tournament, match: Match) -> Match:
    if t.status == "draft":
        raise AppError("Generate and start the tournament first.")
    if not t.draw:
        raise AppError("No draw.")
    if not t.draw.locked:
        raise AppError("Lock the draw before starting matches.")
    if match.status in {"completed", "walkover", "retired", "cancelled"}:
        raise AppError("This match is already finished.")
    if match.player1_is_bye or match.player2_is_bye:
        raise AppError("Bye matches advance automatically.")
    if not match.player1_id or not match.player2_id:
        raise AppError("Both players must be present to start this match.")
    live = [m for m in t.matches if m.status == "live"]
    if opponents_conflict(
        [{"id": m.id, "status": m.status, "player1_id": m.player1_id, "player2_id": m.player2_id} for m in live],
        match.player1_id,
        match.id,
    ) or opponents_conflict(
        [{"id": m.id, "status": m.status, "player1_id": m.player1_id, "player2_id": m.player2_id} for m in live],
        match.player2_id,
        match.id,
    ):
        raise AppError("A player is already in a live match.")
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
    if not t.draw:
        raise AppError("Generate a draw first.")
    if not t.draw.locked:
        raise AppError("Lock the draw before entering results.")
    if match.status in {"completed", "walkover", "retired", "cancelled"} and match.winner_id:
        raise AppError("This match is already completed.")
    if match.player1_is_bye or match.player2_is_bye:
        raise AppError("Bye matches cannot be scored.")
    if not match.player1_id or not match.player2_id:
        raise AppError("Both players must be present to enter a result.")

    result_type = (data.get("result_type") or "normal").lower()
    games = data.get("scores") or data.get("games") or []
    try:
        checked = validate_score(games, t.scoring_rules(), result_type)
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

    if t.format == "single_elimination":
        _advance_persisted(session, t, match)

    remaining = [
        m
        for m in t.matches
        if m.status not in {"completed", "walkover", "retired", "cancelled"}
        and not m.player1_is_bye
        and not m.player2_is_bye
    ]
    if t.format == "single_elimination":
        final = max(t.matches, key=lambda m: m.round_index)
        if final.winner_id:
            t.status = "completed"
    elif t.format == "round_robin" and not remaining:
        t.status = "completed"
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


def assign_court(session: Session, t: Tournament, match: Match, court_id: int | None, scheduled_time: str | None = None) -> Match:
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
            packed = [
                {"id": m.id, "status": m.status, "player1_id": m.player1_id, "player2_id": m.player2_id}
                for m in live
            ]
            if opponents_conflict(packed, match.player1_id, match.id) or opponents_conflict(
                packed, match.player2_id, match.id
            ):
                raise AppError("A player is already in a live match.")
        match.court_id = court.id
    else:
        match.court_id = None
    if scheduled_time is not None:
        match.scheduled_time = scheduled_time.strip() or None
    return match


def results_payload(t: Tournament) -> dict[str, Any]:
    matches = sorted(t.matches, key=lambda m: m.match_number)
    finished = [m for m in matches if m.winner_id and m.result_type != "bye"]
    champion = runner = None
    semis: list[dict[str, Any]] = []
    quarters: list[dict[str, Any]] = []
    if t.format == "single_elimination" and matches:
        final = max(matches, key=lambda m: (m.round_index, m.match_number))
        if final.winner_id:
            champion = _player_public(final.winner)
            runner = _player_public(final.loser)
        for m in matches:
            if m.round_name == "Semifinals" and m.loser_id:
                semis.append(_player_public(m.loser))
            if m.round_name == "Quarterfinals" and m.loser_id:
                quarters.append(_player_public(m.loser))
        # unique
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
    standings = _rr_standings(t) if t.format == "round_robin" else None
    return {
        "champion": champion,
        "runner_up": runner,
        "semifinalists": semis,
        "quarterfinalists": quarters,
        "matches": [match_to_dict(m) for m in finished],
        "standings": standings,
    }


def _rr_standings(t: Tournament) -> list[dict[str, Any]]:
    table: dict[int, dict[str, Any]] = {}
    for p in t.players:
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
    for m in t.matches:
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


def full_payload(t: Tournament) -> dict[str, Any]:
    matches = [match_to_dict(m) for m in sorted(t.matches, key=lambda m: m.match_number)]
    upcoming = [
        m
        for m in matches
        if m["status"] in {"ready", "not_started"}
        and not m["player1_is_bye"]
        and not m["player2_is_bye"]
        and m["player1"]
        and m["player2"]
    ]
    live = [m for m in matches if m["status"] == "live"]
    return {
        "tournament": tournament_to_dict(t, include_nested=True),
        "matches": matches,
        "upcoming": upcoming[:12],
        "live": live,
        "results": results_payload(t),
        "preview": draw_preview(t),
    }
