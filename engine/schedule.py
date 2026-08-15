"""Court assignment and simple day scheduling.

Courts are a scarce resource: if there are 4 courts, at most 4 matches
are on court at once. When a match finishes, the next ready match takes
that court. Times are estimates from day start, average match length,
changeovers, rest breaks, and an optional lunch window.
"""

from __future__ import annotations

import re
from typing import Any

WINNER_RE = re.compile(r"Winner of Match\s+(\d+)", re.I)

DONE = {"completed", "walkover", "retired", "cancelled"}


def parse_hhmm(value: str | None, default: str | None = "09:00") -> int | None:
    raw = (value or "").strip()
    if not raw:
        raw = (default or "").strip()
    if not raw:
        return None
    stamp = raw.split()[0].replace(".", ":")
    parts = stamp.split(":")
    try:
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        return hour * 60 + minute
    except (TypeError, ValueError, IndexError):
        return parse_hhmm(default, None) if default and default != value else None


def normalize_hhmm(value: str | None, default: str | None = None) -> str | None:
    parsed = parse_hhmm(value, default)
    if parsed is None:
        return None
    return format_hhmm(parsed).split()[0]


def format_hhmm(total_minutes: int) -> str:
    days, mins = divmod(max(0, int(total_minutes)), 24 * 60)
    hour, minute = divmod(mins, 60)
    stamp = f"{hour:02d}:{minute:02d}"
    if days:
        stamp += f" +{days}d"
    return stamp


def winner_source_number(source: str | None) -> int | None:
    if not source:
        return None
    found = WINNER_RE.search(source)
    return int(found.group(1)) if found else None


def is_playable(match: dict[str, Any]) -> bool:
    if match.get("player1_is_bye") or match.get("player2_is_bye"):
        return False
    if match.get("result_type") == "bye":
        return False
    if match.get("status") in DONE:
        return False
    return bool(match.get("player1_id") and match.get("player2_id"))


def occupant_ids(match: dict[str, Any]) -> list[int]:
    """People in a match. Prefers person ids so doubles and multi-event rest work."""
    people = match.get("person_ids") or []
    if people:
        return [int(pid) for pid in people if pid]
    out: list[int] = []
    for key in ("player1_id", "player2_id"):
        pid = match.get(key)
        if pid:
            out.append(int(pid))
    return out


def _busy_players(matches: list[dict[str, Any]]) -> set[int]:
    busy: set[int] = set()
    for match in matches:
        if match.get("status") != "live":
            continue
        busy.update(occupant_ids(match))
    return busy


def assign_available_courts(
    matches: list[dict[str, Any]],
    court_ids: list[int],
) -> list[dict[str, Any]]:
    """Give free courts to the next ready matches. Live matches keep their court.

    At most len(court_ids) matches are on court (live or ready-assigned).
    """
    if not court_ids:
        return matches
    court_ids = list(court_ids)
    live = [m for m in matches if m.get("status") == "live"]
    occupied: dict[int, int] = {}
    for match in live:
        cid = match.get("court_id")
        if cid in court_ids and cid not in occupied:
            occupied[int(cid)] = match["id"]
        elif cid not in court_ids:
            # Live but no valid court — park on the first leftover court later.
            pass

    # Live matches without a court take any leftover court first.
    leftovers = [c for c in court_ids if c not in occupied]
    for match in live:
        if match.get("court_id") in occupied and occupied.get(match.get("court_id")) == match["id"]:
            continue
        if not leftovers:
            break
        court = leftovers.pop(0)
        match["court_id"] = court
        occupied[court] = match["id"]

    busy = _busy_players(matches)
    for match in matches:
        if match.get("status") == "ready":
            match["court_id"] = None

    queue = sorted(
        [m for m in matches if m.get("status") == "ready" and is_playable(m)],
        key=lambda m: (int(m.get("round_index") or 0), int(m.get("match_number") or 0)),
    )
    free = [c for c in court_ids if c not in occupied]
    used_players = set(busy)
    for match in queue:
        if not free:
            break
        people = occupant_ids(match)
        if any(pid in used_players for pid in people):
            continue
        court = free.pop(0)
        match["court_id"] = court
        occupied[court] = match["id"]
        used_players.update(people)
    return matches


def _bump_lunch(t0: int, settings: dict[str, Any]) -> int:
    lunch = parse_hhmm(settings.get("lunch_start"), None)
    if lunch is None:
        return t0
    length = int(settings.get("lunch_minutes") or 45)
    if lunch <= t0 < lunch + length:
        return lunch + length
    return t0


def estimate_times(
    matches: list[dict[str, Any]],
    court_count: int,
    settings: dict[str, Any],
) -> list[dict[str, Any]]:
    """Fill scheduled_time on unfinished matches using a court-and-player timeline."""
    n = max(1, int(court_count or 1))
    day_start = parse_hhmm(settings.get("day_start"), "09:00") or 9 * 60
    duration = max(5, int(settings.get("avg_match_minutes") or 25))
    changeover = max(0, int(settings.get("changeover_minutes") or 5))
    min_rest = max(0, int(settings.get("min_rest_minutes") or 0))
    break_every = int(settings.get("break_every_waves") or 0)
    break_minutes = max(0, int(settings.get("break_minutes") or 15))
    slot = duration + changeover

    court_free = [day_start] * n
    court_played = [0] * n
    player_free: dict[int, int] = {}
    match_end: dict[int, int] = {}

    ordered = sorted(matches, key=lambda m: (int(m.get("round_index") or 0), int(m.get("match_number") or 0)))
    for match in ordered:
        if match.get("player1_is_bye") or match.get("player2_is_bye") or match.get("result_type") == "bye":
            match_end[int(match["match_number"])] = day_start
            continue

        t0 = day_start
        people = occupant_ids(match)
        for pid in people:
            t0 = max(t0, player_free.get(pid, day_start))
        for side in (1, 2):
            if match.get(f"player{side}_id"):
                continue
            feeder = winner_source_number(match.get(f"player{side}_source"))
            if feeder and feeder in match_end:
                t0 = max(t0, match_end[feeder])

        court_i = min(range(n), key=lambda i: max(court_free[i], t0))
        start = max(court_free[court_i], t0)
        start = _bump_lunch(start, settings)
        start = max(start, court_free[court_i])
        start = _bump_lunch(start, settings)

        keep_time = match.get("time_locked") or (match.get("status") in DONE and match.get("scheduled_time"))
        if keep_time:
            parsed = parse_hhmm(str(match["scheduled_time"]).split()[0], None)
            if parsed is not None:
                start = parsed
        else:
            match["scheduled_time"] = format_hhmm(start)
        match["expected_time"] = match.get("scheduled_time")

        end = start + slot
        person_free_at = start + duration + min_rest
        court_free[court_i] = max(court_free[court_i], end)
        court_played[court_i] += 1
        match_end[int(match["match_number"])] = end
        for pid in people:
            player_free[pid] = max(player_free.get(pid, day_start), person_free_at)

        if break_every > 0 and break_minutes > 0:
            played = min(court_played)
            if played > 0 and played % break_every == 0 and len(set(court_played)) == 1:
                for i in range(n):
                    court_free[i] = max(court_free[i], end) + break_minutes
    return matches
