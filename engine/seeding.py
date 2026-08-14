"""Single-elimination seeding and slot placement.

Standard bracket seeding (tennis / badminton / NCAA-style):

  8-bracket slots:  [1, 8, 4, 5, 2, 7, 3, 6]

  Seed 1 vs Seed 8
  Seed 4 vs Seed 5
  Seed 2 vs Seed 7
  Seed 3 vs Seed 6

Seed 1 and Seed 2 are in opposite halves. Seeds 1–4 occupy four
different quarters. Lower seeds are nested so they cannot meet early.
"""

from __future__ import annotations

import math
import random
from typing import Any


class DrawError(ValueError):
    pass


def generate_bracket_size(num_players: int) -> int:
    if num_players < 2:
        raise DrawError("A knockout tournament needs at least 2 players.")
    size = 1
    while size < num_players:
        size *= 2
    return size


def calculate_seed_positions(bracket_size: int) -> list[int]:
    """Return the seed number that belongs in each bracket slot 0..n-1."""
    if bracket_size < 2 or (bracket_size & (bracket_size - 1)) != 0:
        raise DrawError("Bracket size must be a power of two.")

    def build(n: int) -> list[int]:
        if n == 1:
            return [1]
        out: list[int] = []
        for seed in build(n // 2):
            out.append(seed)
            out.append(n + 1 - seed)
        return out

    return build(bracket_size)


def empty_slots(bracket_size: int) -> list[dict[str, Any]]:
    positions = calculate_seed_positions(bracket_size)
    return [
        {
            "index": i,
            "seed_number": positions[i],
            "player_id": None,
            "is_bye": False,
        }
        for i in range(bracket_size)
    ]


def place_seeds(slots: list[dict[str, Any]], players: list[dict[str, Any]]) -> None:
    """Place seeded players into their mathematically correct slots."""
    seeded = [p for p in players if p.get("seed")]
    seen: set[int] = set()
    n = len(players)
    for p in seeded:
        seed = int(p["seed"])
        if seed < 1 or seed > n:
            raise DrawError(f"Seed {seed} is not valid for {n} players.")
        if seed in seen:
            raise DrawError(f"Duplicate seed: {seed}.")
        seen.add(seed)
    by_seed = {int(p["seed"]): p for p in seeded}
    for slot in slots:
        player = by_seed.get(slot["seed_number"])
        if player:
            slot["player_id"] = player["id"]
            slot["is_bye"] = False


def place_byes(slots: list[dict[str, Any]], num_byes: int) -> None:
    """Give byes to the lowest virtual seeds so the highest seeds receive them."""
    if num_byes <= 0:
        return
    size = len(slots)
    if num_byes >= size:
        raise DrawError("Too many byes.")
    bye_seeds = [size - i for i in range(num_byes)]
    by_seed = {s["seed_number"]: s for s in slots}
    for seed_num in bye_seeds:
        slot = by_seed[seed_num]
        if slot["player_id"] is not None:
            raise DrawError("Cannot place a bye over a seeded player.")
        slot["is_bye"] = True
        slot["player_id"] = None


def randomize_unseeded_players(
    slots: list[dict[str, Any]],
    players: list[dict[str, Any]],
    rng: random.Random,
) -> None:
    taken = {s["player_id"] for s in slots if s["player_id"] is not None}
    unseeded = [p for p in players if not p.get("seed") and p["id"] not in taken]
    empty = [s for s in slots if s["player_id"] is None and not s["is_bye"]]
    if len(unseeded) != len(empty):
        raise DrawError(
            f"Slot mismatch: {len(unseeded)} unseeded players, {len(empty)} empty slots."
        )
    rng.shuffle(unseeded)
    for slot, player in zip(empty, unseeded):
        slot["player_id"] = player["id"]


def build_draw(
    players: list[dict[str, Any]],
    rng: random.Random | None = None,
) -> dict[str, Any]:
    """Full knockout placement pipeline. Returns slots + bye count + size."""
    names = [str(p.get("name", "")).strip().lower() for p in players]
    if any(not n for n in names):
        raise DrawError("Every player needs a name.")
    n = len(players)
    size = generate_bracket_size(n)
    slots = empty_slots(size)
    place_seeds(slots, players)
    place_byes(slots, size - n)
    randomize_unseeded_players(slots, players, rng or random.Random())
    occupied = sum(1 for s in slots if s["player_id"] is not None)
    byes = sum(1 for s in slots if s["is_bye"])
    if occupied + byes != size:
        raise DrawError("Draw is incomplete.")
    if occupied != n:
        raise DrawError("Not every player was placed in the draw.")
    return {
        "bracket_size": size,
        "byes": byes,
        "slots": slots,
        "rounds": int(math.log2(size)),
    }
