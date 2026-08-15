import random

from engine.bracket import apply_opening_byes, generate_matches
from engine.scoring import ScoreError, validate_score
from engine.seeding import (
    build_draw,
    bye_positions,
    calculate_seed_positions,
    canonical_seed_placement,
    generate_bracket_size,
    max_seeds_for_entries,
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
    assert generate_bracket_size(33) == 64
    assert generate_bracket_size(50) == 64
    assert generate_bracket_size(64) == 64
    assert generate_bracket_size(65) == 128
    assert generate_bracket_size(100) == 128
    assert generate_bracket_size(128) == 128


def test_max_seeds_follows_bwf_bands():
    assert max_seeds_for_entries(8) == 2
    assert max_seeds_for_entries(15) == 2
    assert max_seeds_for_entries(16) == 4
    assert max_seeds_for_entries(31) == 4
    assert max_seeds_for_entries(32) == 8
    assert max_seeds_for_entries(63) == 8
    assert max_seeds_for_entries(64) == 16
    assert max_seeds_for_entries(128) == 16


def test_bwf_canonical_seed_positions():
    eight = canonical_seed_placement(8)
    assert eight[1] == 1
    assert eight[2] == 8
    assert set(canonical_seed_placement(16)[s] for s in (3, 4)) == {5, 12}
    assert set(canonical_seed_placement(32)[s] for s in (3, 4)) == {9, 24}
    assert set(canonical_seed_placement(32)[s] for s in range(5, 9)) == {5, 13, 20, 28}
    assert set(canonical_seed_placement(64)[s] for s in (3, 4)) == {17, 48}
    assert set(canonical_seed_placement(64)[s] for s in range(5, 9)) == {9, 25, 40, 56}
    assert set(canonical_seed_placement(64)[s] for s in range(9, 17)) == {5, 13, 21, 29, 36, 44, 52, 60}
    assert set(canonical_seed_placement(128)[s] for s in (3, 4)) == {33, 96}
    assert set(canonical_seed_placement(128)[s] for s in range(5, 9)) == {17, 49, 80, 112}
    assert set(canonical_seed_placement(128)[s] for s in range(9, 17)) == {9, 25, 41, 57, 72, 88, 104, 120}


def test_seeds_occupy_separate_quarters():
    for size in (8, 16, 32, 64, 128):
        placed = canonical_seed_placement(size)
        quarters = [(placed[seed] - 1) // (size // 4) for seed in (1, 2, 3, 4)]
        assert len(set(quarters)) == 4, size
        assert placed[1] == 1
        assert placed[2] == size
        eighths = [(placed[seed] - 1) // (size // 8) for seed in range(1, 9)]
        assert len(set(eighths)) == 8, size


def test_equivalent_seeds_are_drawn_by_lot_inside_legal_slots():
    rng_a = random.Random(1)
    rng_b = random.Random(2)
    a = calculate_seed_positions(16, 4, rng_a)
    b = calculate_seed_positions(16, 4, rng_b)
    assert a[1] == 1 and a[2] == 16
    assert set(a[s] for s in (3, 4)) == {5, 12}
    assert set(b[s] for s in (3, 4)) == {5, 12}
    eight = calculate_seed_positions(32, 8, random.Random(9))
    assert eight[1] == 1 and eight[2] == 32
    assert set(eight[s] for s in (3, 4)) == {9, 24}
    assert set(eight[s] for s in range(5, 9)) == {5, 13, 20, 28}


def test_eight_player_seeds_top_and_bottom():
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
    assert by_id[1] == 0
    assert by_id[2] == 7
    top = {s["player_id"] for s in slots[:4]}
    bottom = {s["player_id"] for s in slots[4:]}
    assert 1 in top and 2 in bottom
    q = [(by_id[i]) // 2 for i in (1, 2, 3, 4)]
    assert len(set(q)) == 4


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


def _players(n: int, seeds: int) -> list[dict]:
    return [
        {"id": i, "name": f"P{i}", "seed": i if i <= seeds else None}
        for i in range(1, n + 1)
    ]


def _assert_draw_invariants(n: int, seeds: int, rng_seed: int = 0) -> dict:
    plan = build_draw(_players(n, seeds), random.Random(rng_seed))
    size = generate_bracket_size(n)
    assert plan["bracket_size"] == size
    assert plan["byes"] == size - n
    slots = plan["slots"]
    assert len(slots) == size
    ids = [s["player_id"] for s in slots if s["player_id"] is not None]
    assert len(ids) == n
    assert len(set(ids)) == n
    assert set(ids) == set(range(1, n + 1))
    assert sum(1 for s in slots if s["is_bye"]) == size - n
    assert all(not s["is_bye"] or s["player_id"] is None for s in slots)
    by_seed = {s["seed_number"]: s for s in slots if s["seed_number"]}
    if seeds >= 1:
        assert by_seed[1]["index"] == 0
        assert by_seed[1]["player_id"] == 1
    if seeds >= 2:
        assert by_seed[2]["index"] == size - 1
        assert by_seed[2]["player_id"] == 2
    if seeds >= 4:
        qsize = size // 4
        quarters = {by_seed[s]["index"] // qsize for s in (1, 2, 3, 4)}
        assert quarters == {0, 1, 2, 3}
    if seeds >= 8:
        eighths = {by_seed[s]["index"] // (size // 8) for s in range(1, 9)}
        assert len(eighths) == 8
    matches = generate_matches(plan["slots"])
    apply_opening_byes(matches)
    assert len([m for m in matches if m["result_type"] == "bye"]) == size - n
    return plan


def test_generalized_draws_for_arbitrary_fields():
    cases = [
        (3, 2), (4, 2), (5, 2), (6, 2), (7, 2), (8, 2), (8, 4),
        (9, 2), (10, 2), (13, 2), (15, 2), (16, 4), (17, 4),
        (20, 4), (24, 4), (31, 4), (32, 8), (33, 8), (50, 8),
        (63, 8), (64, 16), (65, 16), (100, 16), (128, 16),
    ]
    for n, seeds in cases:
        _assert_draw_invariants(n, seeds, rng_seed=n * 10 + seeds)


def test_official_bye_tables_small_draws():
    assert set(bye_positions(5)) == {2, 4, 7}
    assert set(bye_positions(6)) == {2, 7}
    assert bye_positions(7) == [2]
    assert set(bye_positions(9)) == {2, 4, 6, 8, 11, 13, 15}
    assert set(bye_positions(10)) == {2, 4, 6, 11, 13, 15}
    assert set(bye_positions(13)) == {2, 6, 15}
    assert set(bye_positions(14)) == {2, 15}
    assert bye_positions(15) == [2]
    assert set(bye_positions(24)) == {2, 6, 10, 14, 19, 23, 27, 31}
    assert set(bye_positions(30)) == {2, 31}
    assert set(bye_positions(50)) == {2, 6, 10, 14, 18, 22, 26, 39, 43, 47, 51, 55, 59, 63}


def test_fewer_seeds_than_maximum_are_respected():
    plan = build_draw(_players(32, 4), random.Random(3))
    seeded_slots = [s for s in plan["slots"] if s["seed_number"]]
    assert {s["seed_number"] for s in seeded_slots} == {1, 2, 3, 4}
    assert plan["slots"][0]["player_id"] == 1
    assert plan["slots"][-1]["player_id"] == 2


def test_unseeded_never_overwrite_seeds_or_byes():
    plan = build_draw(_players(10, 2), random.Random(4))
    for slot in plan["slots"]:
        if slot["seed_number"] in {1, 2}:
            assert slot["player_id"] == slot["seed_number"]
            assert not slot["is_bye"]
        if slot["is_bye"]:
            assert slot["player_id"] is None


def test_five_player_advancement_to_champion():
    plan = build_draw(_players(5, 2), random.Random(5))
    matches = generate_matches(plan["slots"])
    apply_opening_byes(matches)
    assert plan["byes"] == 3
    by_num = {m["match_number"]: m for m in matches}

    def play(match):
        from engine.bracket import advance_winner

        winner = match["player1_id"]
        match["winner_id"] = winner
        match["loser_id"] = match["player2_id"]
        match["status"] = "completed"
        match["result_type"] = "normal"
        advance_winner(matches, match["match_number"], winner)

    for _ in range(8):
        ready = [
            m
            for m in matches
            if m["status"] == "ready" and m["player1_id"] and m["player2_id"]
        ]
        if not ready:
            break
        for match in ready:
            play(match)
    final = max(matches, key=lambda m: m["round_index"])
    assert final["winner_id"]


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


def test_completed_match_keeps_same_court_until_next_slot():
    from engine.schedule import estimate_times

    settings = {
        "day_start": "09:00",
        "avg_match_minutes": 25,
        "changeover_minutes": 5,
        "break_every_waves": 0,
        "break_minutes": 0,
        "lunch_start": "",
        "lunch_minutes": 45,
        "min_rest_minutes": 0,
    }
    matches = [
        {
            "id": 1,
            "match_number": 1,
            "round_index": 0,
            "event_id": 1,
            "status": "completed",
            "court_id": 10,
            "scheduled_time": "09:00",
            "player1_id": 1,
            "player2_id": 2,
        },
        {
            "id": 2,
            "match_number": 2,
            "round_index": 0,
            "event_id": 1,
            "status": "ready",
            "court_id": 11,
            "scheduled_time": "09:00",
            "player1_id": 3,
            "player2_id": 4,
        },
        {
            "id": 3,
            "match_number": 3,
            "round_index": 0,
            "event_id": 1,
            "status": "ready",
            "court_id": 10,
            "player1_id": 5,
            "player2_id": 6,
        },
    ]
    estimate_times(matches, 2, settings, court_ids=[10, 11])
    by_id = {m["id"]: m for m in matches}
    assert by_id[1]["scheduled_time"] == "09:00"
    assert by_id[2]["scheduled_time"] == "09:00"
    assert by_id[3]["scheduled_time"] == "09:30"


def test_parse_hhmm_accepts_seconds():
    from engine.schedule import normalize_hhmm, parse_hhmm

    assert parse_hhmm("09:00:00") == 9 * 60
    assert normalize_hhmm("09:00:00") == "09:00"
    assert normalize_hhmm("") is None
