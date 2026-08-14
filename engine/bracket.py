"""Knockout match generation and automatic winner advancement."""

from __future__ import annotations

import math
from typing import Any

from engine.seeding import DrawError


def round_name(round_index: int, total_rounds: int) -> str:
    remaining = 2 ** (total_rounds - round_index)
    names = {
        2: "Final",
        4: "Semifinals",
        8: "Quarterfinals",
        16: "Round of 16",
        32: "Round of 32",
        64: "Round of 64",
        128: "Round of 128",
    }
    return names.get(remaining, f"Round of {remaining}")


def generate_matches(slots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    n = len(slots)
    if n < 2 or (n & (n - 1)) != 0:
        raise DrawError("Bracket size must be a power of two.")
    total_rounds = int(math.log2(n))
    by_round: list[list[dict[str, Any]]] = []
    number = 1
    for r in range(total_rounds):
        count = n // (2 ** (r + 1))
        row: list[dict[str, Any]] = []
        for i in range(count):
            row.append(
                {
                    "round_index": r,
                    "round_name": round_name(r, total_rounds),
                    "match_number": number,
                    "position_in_round": i,
                    "player1_id": None,
                    "player2_id": None,
                    "player1_is_bye": False,
                    "player2_is_bye": False,
                    "player1_source": None,
                    "player2_source": None,
                    "next_match_number": None,
                    "next_slot": None,
                    "status": "not_started",
                    "winner_id": None,
                    "loser_id": None,
                    "result_type": None,
                    "scores": [],
                }
            )
            number += 1
        by_round.append(row)

    for i, match in enumerate(by_round[0]):
        a, b = slots[i * 2], slots[i * 2 + 1]
        _fill_slot(match, 1, a)
        _fill_slot(match, 2, b)

    for r in range(total_rounds - 1):
        for i, match in enumerate(by_round[r]):
            nxt = by_round[r + 1][i // 2]
            slot = 1 if i % 2 == 0 else 2
            match["next_match_number"] = nxt["match_number"]
            match["next_slot"] = slot
            source = f"Winner of Match {match['match_number']}"
            if slot == 1:
                nxt["player1_source"] = source
            else:
                nxt["player2_source"] = source

    matches = [m for row in by_round for m in row]
    for match in matches:
        refresh_match_status(match)
    return matches


def _fill_slot(match: dict[str, Any], side: int, slot: dict[str, Any]) -> None:
    prefix = f"player{side}"
    if slot["is_bye"]:
        match[f"{prefix}_is_bye"] = True
        match[f"{prefix}_id"] = None
        match[f"{prefix}_source"] = "BYE"
    else:
        match[f"{prefix}_is_bye"] = False
        match[f"{prefix}_id"] = slot["player_id"]
        match[f"{prefix}_source"] = None


def refresh_match_status(match: dict[str, Any]) -> None:
    if match.get("status") in {"completed", "walkover", "retired", "cancelled"}:
        if match.get("winner_id") is not None:
            return
    p1 = match.get("player1_id")
    p2 = match.get("player2_id")
    b1 = bool(match.get("player1_is_bye"))
    b2 = bool(match.get("player2_is_bye"))
    if b1 and b2:
        match["status"] = "cancelled"
        return
    if (p1 and p2) or (p1 and b2) or (p2 and b1):
        if (p1 and p2) and not b1 and not b2:
            match["status"] = "ready"
        return
    match["status"] = "not_started"


def advance_winner(
    matches: list[dict[str, Any]],
    from_match_number: int,
    winner_id: int | None,
) -> dict[str, Any] | None:
    """Place the winner into the next match. Returns the next match or None."""
    by_num = {m["match_number"]: m for m in matches}
    match = by_num[from_match_number]
    nxt_num = match.get("next_match_number")
    if not nxt_num or winner_id is None:
        return None
    nxt = by_num[nxt_num]
    slot = match["next_slot"]
    prefix = f"player{slot}"
    nxt[f"{prefix}_id"] = winner_id
    nxt[f"{prefix}_is_bye"] = False
    nxt[f"{prefix}_source"] = None
    if nxt.get("status") not in {"completed", "walkover", "retired"}:
        refresh_match_status(nxt)
        if nxt.get("player1_id") and nxt.get("player2_id"):
            nxt["status"] = "ready"
        elif nxt.get("player1_id") or nxt.get("player2_id"):
            nxt["status"] = "not_started"
    return nxt


def apply_opening_byes(matches: list[dict[str, Any]]) -> None:
    """Auto-advance any player who received a bye. Repeat until stable."""
    by_num = {m["match_number"]: m for m in matches}
    changed = True
    while changed:
        changed = False
        for match in sorted(matches, key=lambda m: m["match_number"]):
            if match.get("winner_id") is not None:
                continue
            p1, p2 = match.get("player1_id"), match.get("player2_id")
            b1, b2 = bool(match.get("player1_is_bye")), bool(match.get("player2_is_bye"))
            if b1 and b2:
                match["status"] = "cancelled"
                match["result_type"] = "bye"
                continue
            winner = None
            loser = None
            if p1 and b2 and not p2:
                winner, loser = p1, None
            elif p2 and b1 and not p1:
                winner, loser = p2, None
            if winner is None:
                continue
            match["winner_id"] = winner
            match["loser_id"] = loser
            match["status"] = "completed"
            match["result_type"] = "bye"
            match["scores"] = []
            changed = True
            nxt = advance_winner(matches, match["match_number"], winner)
            if nxt:
                by_num[nxt["match_number"]] = nxt


def opponents_conflict(matches: list[dict[str, Any]], player_id: int, exclude_id: int | None = None) -> bool:
    """True if the player is already in a live match."""
    for match in matches:
        if exclude_id is not None and match.get("id") == exclude_id:
            continue
        if match.get("status") != "live":
            continue
        if player_id in {match.get("player1_id"), match.get("player2_id")}:
            return True
    return False
