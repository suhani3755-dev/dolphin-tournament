"""Scoring validation driven by tournament configuration — not hard-coded 21."""

from __future__ import annotations

from typing import Any


class ScoreError(ValueError):
    pass


def _as_int(value: Any, label: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ScoreError(f"{label} must be a whole number.") from exc
    if number < 0:
        raise ScoreError(f"{label} cannot be negative.")
    return number


def game_is_complete(a: int, b: int, rules: dict[str, Any], game_index: int = 0) -> bool:
    if a == b:
        return False
    high, low = (a, b) if a > b else (b, a)
    target = int(rules.get("points_per_game") or 21)
    third = rules.get("third_game_points")
    best_of = int(rules.get("best_of") or 3)
    if third and best_of >= 3 and game_index == best_of - 1:
        target = int(third)
    win_by = int(rules.get("win_by") or 2)
    max_score = rules.get("max_score")
    deuce = bool(rules.get("deuce_enabled", True))
    if max_score is not None:
        max_score = int(max_score)
        if high > max_score or low > max_score:
            return False
    if not deuce:
        return high >= target and high > low
    if max_score is not None and high == max_score and high > low:
        return True
    return high >= target and (high - low) >= win_by


def game_winner(a: int, b: int, rules: dict[str, Any], game_index: int = 0) -> int | None:
    if not game_is_complete(a, b, rules, game_index):
        return None
    return 1 if a > b else 2


def explain_game(a: int, b: int, rules: dict[str, Any], game_index: int = 0) -> str | None:
    """Return an error string if this finished-game score is illegal."""
    target = int(rules.get("points_per_game") or 21)
    third = rules.get("third_game_points")
    best_of = int(rules.get("best_of") or 3)
    if third and best_of >= 3 and game_index == best_of - 1:
        target = int(third)
    win_by = int(rules.get("win_by") or 2)
    max_score = rules.get("max_score")
    deuce = bool(rules.get("deuce_enabled", True))
    high, low = (a, b) if a >= b else (b, a)
    if max_score is not None and (a > int(max_score) or b > int(max_score)):
        return f"Scores cannot exceed the {max_score}-point cap."
    if a == b:
        return f"{a}-{b} is a tie. A game needs a winner."
    if not deuce:
        if high < target:
            return f"A game must reach {target} points."
        return None
    if high < target:
        return f"A game must reach {target} points."
    if max_score is not None and high == int(max_score):
        return None
    if (high - low) < win_by:
        cap = f" (cap {max_score})" if max_score else ""
        return f"Must win by {win_by}{cap}. {a}-{b} is not a finished game."
    return None


def validate_score(
    games: list[list[Any]],
    rules: dict[str, Any],
    result_type: str = "normal",
) -> dict[str, Any]:
    """Validate a full match. Returns winner_side (1 or 2) and cleaned games."""
    result_type = (result_type or "normal").lower()
    if result_type not in {
        "normal",
        "walkover",
        "retirement",
        "disqualification",
        "no_show",
        "bye",
    }:
        raise ScoreError(f"Unknown result type: {result_type}.")

    best_of = int(rules.get("best_of") or 3)
    if best_of not in {1, 3, 5}:
        raise ScoreError("Best-of must be 1, 3, or 5.")
    wins_needed = best_of // 2 + 1

    if result_type != "normal":
        cleaned = []
        for i, game in enumerate(games or []):
            if not game or (game[0] in (None, "") and game[1] in (None, "")):
                continue
            a = _as_int(game[0], f"Game {i + 1} player 1")
            b = _as_int(game[1], f"Game {i + 1} player 2")
            cleaned.append([a, b])
        return {"games": cleaned, "winner_side": None, "wins": [0, 0]}

    if not games:
        raise ScoreError("Enter at least one game score.")

    cleaned: list[list[int]] = []
    wins = [0, 0]
    for i, game in enumerate(games):
        if not game or len(game) < 2:
            raise ScoreError(f"Game {i + 1} needs two scores.")
        if game[0] in (None, "") and game[1] in (None, ""):
            continue
        a = _as_int(game[0], f"Game {i + 1} player 1")
        b = _as_int(game[1], f"Game {i + 1} player 2")
        err = explain_game(a, b, rules, i)
        if err:
            raise ScoreError(err)
        if not game_is_complete(a, b, rules, i):
            raise ScoreError(f"Game {i + 1} ({a}-{b}) is not complete.")
        if wins[0] >= wins_needed or wins[1] >= wins_needed:
            raise ScoreError("Extra games were entered after the match was already won.")
        cleaned.append([a, b])
        if a > b:
            wins[0] += 1
        else:
            wins[1] += 1

    if wins[0] < wins_needed and wins[1] < wins_needed:
        raise ScoreError(f"Match is not over. First to {wins_needed} games wins.")
    if wins[0] >= wins_needed and wins[1] >= wins_needed:
        raise ScoreError("Both players cannot have won the match.")
    winner_side = 1 if wins[0] >= wins_needed else 2
    return {"games": cleaned, "winner_side": winner_side, "wins": wins}


def match_winner_from_scores(games: list[list[int]], rules: dict[str, Any]) -> int:
    result = validate_score(games, rules, "normal")
    return int(result["winner_side"])
