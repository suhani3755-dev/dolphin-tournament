"""Tournament engine — seeding, scoring, brackets, advancement."""

from engine.bracket import (
    advance_winner,
    apply_opening_byes,
    generate_matches,
    round_name,
)
from engine.scoring import match_winner_from_scores, validate_score
from engine.seeding import (
    build_draw,
    bye_positions,
    calculate_seed_positions,
    canonical_seed_placement,
    generate_bracket_size,
    max_seeds_for_entries,
    place_byes,
    place_seeds,
    randomize_unseeded_players,
)

__all__ = [
    "advance_winner",
    "apply_opening_byes",
    "build_draw",
    "bye_positions",
    "calculate_seed_positions",
    "canonical_seed_placement",
    "generate_bracket_size",
    "generate_matches",
    "match_winner_from_scores",
    "max_seeds_for_entries",
    "place_byes",
    "place_seeds",
    "randomize_unseeded_players",
    "round_name",
    "validate_score",
]
