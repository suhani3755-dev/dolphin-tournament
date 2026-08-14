import random

from engine.bracket import apply_opening_byes, generate_matches
from engine.scoring import ScoreError, validate_score
from engine.seeding import (
    DrawError,
    build_draw,
    calculate_seed_positions,
    generate_bracket_size,
)


def test_bracket_sizes():
    assert generate_bracket_size(2) == 2
    assert generate_bracket_size(3) == 4
    assert generate_bracket_size(4) == 4
    assert generate_bracket_size(5) == 8
    assert generate_bracket_size(8) == 8
    assert generate_bracket_size(9) == 16
    assert generate_bracket_size(16) == 16
    assert generate_bracket_size(17) == 32
    assert generate_bracket_size(32) == 32


def test_standard_seed_positions():
    assert calculate_seed_positions(2) == [1, 2]
    assert calculate_seed_positions(4) == [1, 4, 2, 3]
    assert calculate_seed_positions(8) == [1, 8, 4, 5, 2, 7, 3, 6]
    assert calculate_seed_positions(16) == [1, 16, 8, 9, 4, 13, 5, 12, 2, 15, 7, 10, 3, 14, 6, 11]


def test_seeds_occupy_separate_quarters():
    for size in (8, 16, 32, 64, 128):
        positions = calculate_seed_positions(size)
        quarters = [positions.index(seed) // (size // 4) for seed in (1, 2, 3, 4)]
        assert len(set(quarters)) == 4, size
        half = size // 2
        assert positions.index(1) < half
        assert positions.index(2) >= half


def test_eight_player_four_seeds_opposite_halves():
    players = [
        {"id": 1, "name": "A", "seed": 1},
        {"id": 2, "name": "B", "seed": 2},
        {"id": 3, "name": "C", "seed": 3},
        {"id": 4, "name": "D", "seed": 4},
        {"id": 5, "name": "E", "seed": None},
        {"id": 6, "name": "F", "seed": None},
        {"id": 7, "name": "G", "seed": None},
        {"id": 8, "name": "H", "seed": None},
    ]
    plan = build_draw(players, random.Random(1))
    slots = plan["slots"]
    by_id = {s["player_id"]: s["index"] for s in slots}
    assert by_id[1] == 0  # seed 1 top of bracket
    assert by_id[2] == 4  # seed 2 opposite half
    assert by_id[4] == 2  # seed 4
    assert by_id[3] == 6  # seed 3
    top = {s["player_id"] for s in slots[:4]}
    bottom = {s["player_id"] for s in slots[4:]}
    assert 1 in top and 2 in bottom
    assert 4 in top and 3 in bottom


def test_byes_go_to_top_seeds():
    players = [
        {"id": 1, "name": "A", "seed": 1},
        {"id": 2, "name": "B", "seed": 2},
        {"id": 3, "name": "C", "seed": 3},
        {"id": 4, "name": "D", "seed": 4},
        {"id": 5, "name": "E", "seed": None},
        {"id": 6, "name": "F", "seed": None},
    ]
    plan = build_draw(players, random.Random(0))
    assert plan["byes"] == 2
    matches = generate_matches(plan["slots"])
    apply_opening_byes(matches)
    r1 = [m for m in matches if m["round_index"] == 0]
    bye_matches = [m for m in r1 if m["result_type"] == "bye"]
    assert len(bye_matches) == 2
    winners = {m["winner_id"] for m in bye_matches}
    assert winners == {1, 2}


def test_score_validation_badminton_defaults():
    rules = {
        "best_of": 3,
        "points_per_game": 21,
        "deuce_enabled": True,
        "win_by": 2,
        "max_score": 30,
    }
    ok = validate_score([[21, 17], [21, 14]], rules)
    assert ok["winner_side"] == 1
    try:
        validate_score([[21, 21]], rules)
        assert False, "21-21 should fail"
    except ScoreError:
        pass
    try:
        validate_score([[21, 20]], rules)
        assert False, "21-20 should fail"
    except ScoreError:
        pass
    ok30 = validate_score([[30, 29], [18, 21], [21, 19]], rules)
    assert ok30["winner_side"] == 1
    try:
        validate_score([[31, 29]], rules)
        assert False, "above cap should fail"
    except ScoreError:
        pass


def test_three_game_match_and_early_stop():
    rules = {
        "best_of": 3,
        "points_per_game": 21,
        "deuce_enabled": True,
        "win_by": 2,
        "max_score": 30,
    }
    ok = validate_score([[21, 19], [19, 21], [21, 15]], rules)
    assert ok["winner_side"] == 1
    try:
        validate_score([[21, 10], [21, 8], [21, 5]], rules)
        assert False, "extra game after match already won"
    except ScoreError:
        pass


def test_advancement_through_bracket():
    players = [{"id": i, "name": chr(64 + i), "seed": i if i <= 4 else None} for i in range(1, 9)]
    plan = build_draw(players, random.Random(7))
    matches = generate_matches(plan["slots"])
    apply_opening_byes(matches)
    by_num = {m["match_number"]: m for m in matches}

    def play(num: int, winner_id: int) -> None:
        from engine.bracket import advance_winner

        m = by_num[num]
        m["winner_id"] = winner_id
        m["status"] = "completed"
        m["result_type"] = "normal"
        other = m["player2_id"] if winner_id == m["player1_id"] else m["player1_id"]
        m["loser_id"] = other
        advance_winner(matches, num, winner_id)

    # Round of 8: matches 1-4
    for m in matches:
        if m["round_index"] == 0:
            play(m["match_number"], m["player1_id"])
    semis = [m for m in matches if m["round_name"] == "Semifinals"]
    assert all(m["player1_id"] and m["player2_id"] for m in semis)
    for m in semis:
        play(m["match_number"], m["player1_id"])
    final = [m for m in matches if m["round_name"] == "Final"][0]
    assert final["player1_id"] and final["player2_id"]
    play(final["match_number"], final["player1_id"])
    assert final["winner_id"] == final["player1_id"]
