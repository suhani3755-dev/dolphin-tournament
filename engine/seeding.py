"""BWF knockout seeding, bye placement, and unseeded fill.

Placement follows the BWF General Competition Regulations:

- Bracket size is the next power of two at or above the entry count.
- Byes = bracketSize - entries.
- Seed 1 at the top of the draw; seed 2 at the bottom.
- Seeds 3–4 drawn by lot into the remaining quarters.
- Seeds 5–8 drawn by lot into the remaining eighths.
- Seeds 9–16 drawn by lot into the remaining sixteenths; same pattern thereafter.
- Seeds in the top half sit at the top of their section; seeds in the bottom
  half sit at the bottom of their section.
- Byes go to first-round opponents of those canonical seed positions, so
  higher seeds are protected first.

Positions are 1-indexed on the draw sheet (slot index = position - 1).
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


def max_seeds_for_entries(entry_count: int) -> int:
    """BWF maximum seeds for a given number of entries (GCR 11.7 / 12.6)."""
    if entry_count < 16:
        return 2
    if entry_count < 32:
        return 4
    if entry_count < 64:
        return 8
    if entry_count < 129:
        return 16
    return 32


def _first_round_opponent(position: int) -> int:
    return position + 1 if position % 2 == 1 else position - 1


def _is_power_of_two(n: int) -> bool:
    return n >= 2 and (n & (n - 1)) == 0


def canonical_seed_placement(bracket_size: int) -> dict[int, int]:
    """Map every virtual seed 1..bracket_size to a 1-indexed draw position.

    Equivalent seed groups are assigned deterministically: remaining top-half
    sections first (top of section), then remaining bottom-half sections
    (bottom of section), interleaved from the outside.
    """
    if not _is_power_of_two(bracket_size):
        raise DrawError("Bracket size must be a power of two.")
    placed: dict[int, int] = {1: 1, 2: bracket_size}
    occupied = {1, bracket_size}
    group = 4
    while group <= bracket_size:
        num_sections = group
        length = bracket_size // num_sections
        empty: list[int] = []
        for i in range(num_sections):
            start = i * length + 1
            end = start + length - 1
            if any(start <= pos <= end for pos in occupied):
                continue
            empty.append(start if start <= bracket_size // 2 else end)
        top = sorted(pos for pos in empty if pos <= bracket_size // 2)
        bottom = sorted((pos for pos in empty if pos > bracket_size // 2), reverse=True)
        ordered: list[int] = []
        for i in range(max(len(top), len(bottom))):
            if i < len(top):
                ordered.append(top[i])
            if i < len(bottom):
                ordered.append(bottom[i])
        seeds = list(range(group // 2 + 1, group + 1))
        if len(seeds) != len(ordered):
            raise DrawError("Failed to derive BWF seed sections.")
        for seed, pos in zip(seeds, ordered):
            placed[seed] = pos
            occupied.add(pos)
        group *= 2
    return placed


def group_positions(bracket_size: int, group_size: int) -> list[int]:
    """Valid 1-indexed positions for a BWF seed group (2, 4, 8, 16, ...)."""
    if group_size == 2:
        return [1, bracket_size]
    placed = canonical_seed_placement(bracket_size)
    lo = group_size // 2 + 1
    return [placed[seed] for seed in range(lo, group_size + 1)]


def calculate_seed_positions(
    entry_count: int,
    seed_count: int | None = None,
    rng: random.Random | None = None,
) -> dict[int, int]:
    """Return {seed_number: 1-indexed position} for seeds 1..seed_count.

    Equivalent groups (3/4, 5–8, 9–16, ...) are shuffled among their legal
    section slots when rng is provided. Separation of sections is preserved.
    """
    size = generate_bracket_size(entry_count)
    if seed_count is None:
        seed_count = min(max_seeds_for_entries(entry_count), entry_count)
    if seed_count < 0:
        raise DrawError("Seed count cannot be negative.")
    if seed_count > entry_count:
        raise DrawError(f"Cannot place {seed_count} seeds among {entry_count} players.")
    if seed_count > size:
        raise DrawError("Too many seeds for the bracket.")
    canonical = canonical_seed_placement(size)
    result: dict[int, int] = {}
    if seed_count >= 1:
        result[1] = canonical[1]
    if seed_count >= 2:
        result[2] = canonical[2]
    group = 4
    while group // 2 < seed_count:
        lo = group // 2 + 1
        hi = min(group, seed_count)
        seeds = list(range(lo, hi + 1))
        positions = [canonical[s] for s in range(lo, group + 1)]
        if rng is not None:
            rng.shuffle(positions)
        for seed, pos in zip(seeds, positions):
            result[seed] = pos
        group *= 2
        if group > size:
            break
    return result


def bye_positions(entry_count: int) -> list[int]:
    """1-indexed bye slots: first-round opponents of canonical seeds 1..bye_count."""
    size = generate_bracket_size(entry_count)
    n_byes = size - entry_count
    canonical = canonical_seed_placement(size)
    seed_at = {pos: seed for seed, pos in canonical.items()}
    out: list[int] = []
    for seed in range(1, size + 1):
        if len(out) >= n_byes:
            break
        opp = _first_round_opponent(canonical[seed])
        if opp in seed_at and seed_at[opp] <= seed:
            continue
        out.append(opp)
    if len(out) != n_byes:
        raise DrawError("Could not place the required number of byes.")
    return out


def empty_slots(bracket_size: int) -> list[dict[str, Any]]:
    return [
        {
            "index": i,
            "seed_number": None,
            "player_id": None,
            "is_bye": False,
        }
        for i in range(bracket_size)
    ]


def _validate_seeds(players: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    seeded = [p for p in players if p.get("seed")]
    seen: set[int] = set()
    n = len(players)
    by_seed: dict[int, dict[str, Any]] = {}
    for player in seeded:
        seed = int(player["seed"])
        if seed < 1 or seed > n:
            raise DrawError(f"Seed {seed} is not valid for {n} players.")
        if seed in seen:
            raise DrawError(f"Duplicate seed: {seed}.")
        seen.add(seed)
        by_seed[seed] = player
    return by_seed


def _positions_for_actual_seeds(
    bracket_size: int,
    seed_numbers: list[int],
    rng: random.Random,
) -> dict[int, int]:
    """Place whatever seeds the organizer assigned, using BWF section slots."""
    if not seed_numbers:
        return {}
    canonical = canonical_seed_placement(bracket_size)
    assigned: dict[int, int] = {}
    numbers = sorted(seed_numbers)
    if 1 in numbers:
        assigned[1] = canonical[1]
    if 2 in numbers:
        assigned[2] = canonical[2]
    group = 4
    remaining = [s for s in numbers if s not in assigned]
    while remaining and group <= bracket_size:
        lo, hi = group // 2 + 1, group
        in_group = [s for s in remaining if lo <= s <= hi]
        slots = [canonical[s] for s in range(lo, hi + 1)]
        taken = set(assigned.values())
        free = [p for p in slots if p not in taken]
        rng.shuffle(free)
        if len(in_group) > len(free):
            raise DrawError("Not enough BWF section slots for the assigned seeds.")
        for seed, pos in zip(in_group, free):
            assigned[seed] = pos
        remaining = [s for s in remaining if s not in assigned]
        group *= 2
    if remaining:
        # Seeds beyond the last full group (should not happen if seed <= bracket).
        leftover = [i + 1 for i in range(bracket_size) if (i + 1) not in assigned.values()]
        rng.shuffle(leftover)
        for seed, pos in zip(remaining, leftover):
            assigned[seed] = pos
    return assigned


def place_seeds(
    slots: list[dict[str, Any]],
    players: list[dict[str, Any]],
    rng: random.Random | None = None,
) -> None:
    """Place seeded players on BWF section positions (equivalent seeds by lot)."""
    by_seed = _validate_seeds(players)
    if not by_seed:
        return
    size = len(slots)
    positions = _positions_for_actual_seeds(size, list(by_seed), rng or random.Random())
    for seed, pos in positions.items():
        slot = slots[pos - 1]
        if slot["player_id"] is not None or slot["is_bye"]:
            raise DrawError("Cannot place a seed over an occupied draw position.")
        slot["player_id"] = by_seed[seed]["id"]
        slot["seed_number"] = seed
        slot["is_bye"] = False


def place_byes(slots: list[dict[str, Any]], num_byes: int) -> None:
    """Place byes on first-round opponents of canonical BWF seed positions."""
    if num_byes <= 0:
        return
    size = len(slots)
    if num_byes >= size:
        raise DrawError("Too many byes.")
    entries = size - num_byes
    for pos in bye_positions(entries):
        slot = slots[pos - 1]
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
    ids = [p["id"] for p in players]
    if len(ids) != len(set(ids)):
        raise DrawError("Every player must appear exactly once.")
    n = len(players)
    size = generate_bracket_size(n)
    rng = rng or random.Random()
    slots = empty_slots(size)
    place_seeds(slots, players, rng)
    place_byes(slots, size - n)
    randomize_unseeded_players(slots, players, rng)
    occupied = sum(1 for s in slots if s["player_id"] is not None)
    byes = sum(1 for s in slots if s["is_bye"])
    if occupied + byes != size:
        raise DrawError("Draw is incomplete.")
    if occupied != n:
        raise DrawError("Not every player was placed in the draw.")
    placed_ids = [s["player_id"] for s in slots if s["player_id"] is not None]
    if len(placed_ids) != len(set(placed_ids)):
        raise DrawError("A player was placed more than once.")
    return {
        "bracket_size": size,
        "byes": byes,
        "slots": slots,
        "rounds": int(math.log2(size)),
    }
