from __future__ import annotations

import os

import pytest
from sqlalchemy.orm import Session

import db
import services as svc


@pytest.fixture
def session(tmp_path, monkeypatch):
    url = "sqlite:///" + str(tmp_path / "test.db")
    monkeypatch.setenv("DATABASE_URL", url)
    db.ENGINE = None
    db.SessionLocal = None
    db.init_engine(url)
    sess = db.get_session()
    yield sess
    sess.close()


def _make_eight(session: Session) -> tuple:
    t = svc.create_tournament(session, {"name": "Open", "sport": "badminton"})
    svc.update_tournament(
        session,
        t,
        {
            "format": "single_elimination",
            "best_of": 3,
            "points_per_game": 21,
            "deuce_enabled": True,
            "win_by": 2,
            "max_score": 30,
            "num_courts": 2,
        },
    )
    names = [
        ("Aria", 1, 1),
        ("Bryn", 2, 2),
        ("Cora", 3, 3),
        ("Dina", 4, 4),
        ("Eden", None, 10),
        ("Faye", None, 11),
        ("Gia", None, 12),
        ("Hana", None, 13),
    ]
    for name, seed, ranking in names:
        svc.add_player(session, t, {"name": name, "club": "Dolphin", "seed": seed, "ranking": ranking})
    session.commit()
    t = svc.load_tournament(session, t.id)
    return t, session


def test_full_eight_player_lifecycle(session):
    t, session = _make_eight(session)
    draw = svc.generate_draw(session, t, rng_seed=42)
    session.commit()
    t = svc.load_tournament(session, t.id)
    slots = {s["seed_number"]: s["player_id"] for s in t.draw.slots_json}
    by_id = {p.id: p for p in t.players}
    seed_of = {p.seed: p.id for p in t.players if p.seed}
    assert slots[1] == seed_of[1]
    assert slots[2] == seed_of[2]
    assert slots[3] == seed_of[3]
    assert slots[4] == seed_of[4]
    svc.lock_draw(session, t)
    svc.start_tournament(session, t)
    session.commit()
    t = svc.load_tournament(session, t.id)

    def play_ready():
        t_local = svc.load_tournament(session, t.id)
        ready = [
            m
            for m in t_local.matches
            if m.status == "ready" and m.player1_id and m.player2_id
        ]
        for match in ready:
            svc.start_match(session, t_local, match)
            svc.enter_result(
                session,
                t_local,
                match,
                {"result_type": "normal", "scores": [[21, 15], [21, 12]]},
            )
        session.commit()
        return ready

    played = 0
    for _ in range(8):
        batch = play_ready()
        played += len(batch)
        if not batch:
            break
    t = svc.load_tournament(session, t.id)
    final = max(t.matches, key=lambda m: m.round_index)
    assert final.winner_id
    assert t.status == "completed"
    results = svc.results_payload(t)
    assert results["champion"]["id"] == final.winner_id
    assert played == 7  # 4 QF + 2 SF + 1 F


def test_non_power_of_two_byes(session):
    t = svc.create_tournament(session, {"name": "Sixes"})
    svc.update_tournament(session, t, {"format": "single_elimination"})
    for i, name in enumerate(["P1", "P2", "P3", "P4", "P5", "P6"], start=1):
        svc.add_player(session, t, {"name": name, "seed": i if i <= 4 else None})
    svc.generate_draw(session, t, rng_seed=1)
    session.commit()
    t = svc.load_tournament(session, t.id)
    assert t.draw.byes == 2
    bye_matches = [m for m in t.matches if m.result_type == "bye"]
    assert len(bye_matches) == 2
    winners = {m.winner_id for m in bye_matches}
    seeds = {p.id: p.seed for p in t.players}
    assert {seeds[w] for w in winners} == {1, 2}


def test_four_and_sixteen_player_draws(session):
    for n in (4, 16, 32):
        t = svc.create_tournament(session, {"name": f"Field {n}"})
        svc.update_tournament(session, t, {"format": "single_elimination"})
        for i in range(1, n + 1):
            svc.add_player(session, t, {"name": f"P{i}", "seed": i if i <= 4 else None})
        svc.generate_draw(session, t, rng_seed=n)
        session.commit()
        t = svc.load_tournament(session, t.id)
        assert t.draw.bracket_size == n
        assert t.draw.byes == 0
        real = [m for m in t.matches if m.result_type != "bye"]
        assert len(real) == n - 1
        slots = {s["seed_number"]: s["player_id"] for s in t.draw.slots_json}
        seed_of = {p.seed: p.id for p in t.players if p.seed}
        assert slots[1] == seed_of[1]
        assert slots[2] == seed_of[2]


def test_locked_draw_blocks_player_edits(session):
    t, session = _make_eight(session)
    svc.generate_draw(session, t, rng_seed=1)
    svc.lock_draw(session, t)
    session.commit()
    t = svc.load_tournament(session, t.id)
    with pytest.raises(svc.AppError):
        svc.add_player(session, t, {"name": "Zed"})
    with pytest.raises(svc.AppError):
        svc.generate_draw(session, t)


def test_walkover_advances(session):
    t, session = _make_eight(session)
    svc.generate_draw(session, t, rng_seed=3)
    svc.lock_draw(session, t)
    session.commit()
    t = svc.load_tournament(session, t.id)
    match = next(m for m in t.matches if m.status == "ready")
    svc.enter_result(
        session,
        t,
        match,
        {"result_type": "walkover", "winner_id": match.player1_id, "scores": []},
    )
    session.commit()
    t = svc.load_tournament(session, t.id)
    nxt = session.get(type(match), match.next_match_id)
    assert nxt.player1_id == match.player1_id or nxt.player2_id == match.player1_id
