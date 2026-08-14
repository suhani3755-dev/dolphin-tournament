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


def test_assign_courts_caps_and_skips_busy_players():
    from engine.schedule import assign_available_courts

    matches = [
        {"id": 1, "status": "ready", "round_index": 0, "match_number": 1, "player1_id": 1, "player2_id": 2, "court_id": None},
        {"id": 2, "status": "ready", "round_index": 0, "match_number": 2, "player1_id": 3, "player2_id": 4, "court_id": None},
        {"id": 3, "status": "ready", "round_index": 0, "match_number": 3, "player1_id": 5, "player2_id": 6, "court_id": None},
        {"id": 4, "status": "live", "round_index": 0, "match_number": 4, "player1_id": 7, "player2_id": 8, "court_id": 10},
        {"id": 5, "status": "ready", "round_index": 1, "match_number": 5, "player1_id": 7, "player2_id": 9, "court_id": None},
    ]
    assign_available_courts(matches, [10, 11])
    assert matches[3]["court_id"] == 10
    assigned_ready = [m for m in matches if m["status"] == "ready" and m["court_id"]]
    assert len(assigned_ready) == 1
    assert assigned_ready[0]["id"] == 1
    assert matches[4]["court_id"] is None


def test_estimate_times_waves_and_lunch():
    from engine.schedule import estimate_times

    matches = [
        {
            "id": i,
            "match_number": i,
            "round_index": 0,
            "status": "ready",
            "player1_id": i * 2,
            "player2_id": i * 2 + 1,
            "player1_source": None,
            "player2_source": None,
        }
        for i in range(1, 5)
    ]
    settings = {
        "day_start": "09:00",
        "avg_match_minutes": 20,
        "changeover_minutes": 10,
        "break_every_waves": 0,
        "break_minutes": 0,
        "lunch_start": "",
        "lunch_minutes": 45,
    }
    estimate_times(matches, 2, settings)
    assert [m["scheduled_time"] for m in matches] == ["09:00", "09:00", "09:30", "09:30"]

    lunch_matches = [dict(m, scheduled_time=None) for m in matches]
    lunch_settings = {**settings, "day_start": "11:50", "lunch_start": "12:00", "lunch_minutes": 45}
    estimate_times(lunch_matches, 2, lunch_settings)
    assert lunch_matches[0]["scheduled_time"] == "11:50"
    assert lunch_matches[1]["scheduled_time"] == "11:50"
    assert lunch_matches[2]["scheduled_time"] == "12:45"
    assert lunch_matches[3]["scheduled_time"] == "12:45"


def test_parse_hhmm_accepts_seconds():
    from engine.schedule import normalize_hhmm, parse_hhmm

    assert parse_hhmm("09:00:00") == 9 * 60
    assert normalize_hhmm("09:00:00") == "09:00"
    assert normalize_hhmm("") is None
