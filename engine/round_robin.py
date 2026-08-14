"""Simple round-robin pairings (circle method)."""

from __future__ import annotations

from typing import Any


def generate_round_robin(player_ids: list[int]) -> list[dict[str, Any]]:
    ids = list(player_ids)
    if len(ids) < 2:
        raise ValueError("Round robin needs at least 2 players.")
    bye = None
    if len(ids) % 2 == 1:
        bye = "BYE"
        ids.append(bye)
    n = len(ids)
    rounds = n - 1
    half = n // 2
    matches: list[dict[str, Any]] = []
    number = 1
    rotation = ids[:]
    for r in range(rounds):
        for i in range(half):
            a, b = rotation[i], rotation[n - 1 - i]
            if a == bye or b == bye:
                continue
            matches.append(
                {
                    "round_index": r,
                    "round_name": f"Round {r + 1}",
                    "match_number": number,
                    "position_in_round": i,
                    "player1_id": a,
                    "player2_id": b,
                    "player1_is_bye": False,
                    "player2_is_bye": False,
                    "player1_source": None,
                    "player2_source": None,
                    "next_match_number": None,
                    "next_slot": None,
                    "status": "ready",
                    "winner_id": None,
                    "loser_id": None,
                    "result_type": None,
                    "scores": [],
                }
            )
            number += 1
        rotation = [rotation[0]] + [rotation[-1]] + rotation[1:-1]
    return matches
