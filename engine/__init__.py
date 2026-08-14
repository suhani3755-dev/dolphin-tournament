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
    calculate_seed_positions,
    generate_bracket_size,
    place_byes,
    place_seeds,
    randomize_unseeded_players,
)

__all__ = [
    "advance_winner",
    "apply_opening_byes",
    "build_draw",
    "calculate_seed_positions",
    "generate_bracket_size",
    "generate_matches",
    "match_winner_from_scores",
    "place_byes",
    "place_seeds",
    "randomize_unseeded_players",
    "round_name",
    "validate_score",
]
