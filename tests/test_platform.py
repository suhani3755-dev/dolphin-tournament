from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

import db
import services as svc


@pytest.fixture
def session(tmp_path, monkeypatch):
    url = "sqlite:///" + str(tmp_path / "platform.db")
    monkeypatch.setenv("DATABASE_URL", url)
    db.ENGINE = None
    db.SessionLocal = None
    db.init_engine(url)
    sess = db.get_session()
    yield sess
    sess.close()


def test_default_event_and_same_person_two_events(session: Session):
    t = svc.create_tournament(session, {"name": "Dolphin Open 2026"})
    session.commit()
    t = svc.load_tournament(session, t.id)
    assert len(t.events) == 1
    main = t.events[0]
    ws = svc.create_event(session, t, {"category": "WS"})
    wd = svc.create_event(session, t, {"category": "WD"})
    xd = svc.create_event(session, t, {"category": "XD"})
    session.commit()
    t = svc.load_tournament(session, t.id)
    names = {e.name for e in t.events}
    assert "Women's Singles" in names
    assert "Women's Doubles" in names
    assert "Mixed Doubles" in names

    svc.add_player(session, t, {"name": "Suhani", "club": "Dolphin", "event_id": ws.id, "seed": 1})
    svc.add_player(
        session,
        t,
        {"name": "Suhani", "club": "Dolphin", "partner_name": "Aria", "event_id": wd.id, "seed": 1},
    )
    svc.add_player(
        session,
        t,
        {"name": "Suhani", "club": "Dolphin", "partner_name": "Bryn", "event_id": xd.id, "seed": 1},
    )
    session.commit()
    t = svc.load_tournament(session, t.id)
    people = [p for p in t.people if p.name == "Suhani"]
    assert len(people) == 1
    suhani = people[0]
    assert len(suhani.entry_links) == 3
    assert {p.name for p in t.players} >= {"Suhani", "Suhani / Aria", "Suhani / Bryn"}
    # Same seed is allowed in different events.
    assert sum(1 for p in t.players if p.seed == 1) == 3
    profile = svc.person_page(t, suhani)
    assert len(profile["entries"]) == 3


def test_generate_draw_is_event_scoped(session: Session):
    t = svc.create_tournament(session, {"name": "Split"})
    svc.update_tournament(session, t, {"format": "single_elimination"})
    ws = svc.create_event(session, t, {"category": "WS"})
    ms = svc.create_event(session, t, {"category": "MS"})
    for i in range(1, 5):
        svc.add_player(session, t, {"name": f"W{i}", "event_id": ws.id, "seed": i})
        svc.add_player(session, t, {"name": f"M{i}", "event_id": ms.id, "seed": i})
    svc.generate_draw(session, t, rng_seed=1, event_id=ws.id)
    session.commit()
    t = svc.load_tournament(session, t.id)
    ws_matches = [m for m in t.matches if m.event_id == ws.id]
    ms_matches = [m for m in t.matches if m.event_id == ms.id]
    assert ws_matches
    assert not ms_matches
    svc.generate_draw(session, t, rng_seed=2, event_id=ms.id)
    session.commit()
    t = svc.load_tournament(session, t.id)
    assert [m for m in t.matches if m.event_id == ws.id]
    assert [m for m in t.matches if m.event_id == ms.id]


def test_person_conflict_and_admin_override(session: Session):
    t = svc.create_tournament(session, {"name": "Busy Day"})
    svc.update_tournament(
        session,
        t,
        {
            "format": "single_elimination",
            "num_courts": 2,
            "avg_match_minutes": 25,
            "min_rest_minutes": 30,
            "auto_assign_courts": False,
        },
    )
    ws = svc.create_event(session, t, {"category": "WS"})
    wd = svc.create_event(session, t, {"category": "WD"})
    svc.add_player(session, t, {"name": "Suhani", "event_id": ws.id, "seed": 1})
    svc.add_player(session, t, {"name": "Cora", "event_id": ws.id, "seed": 2})
    svc.add_player(session, t, {"name": "Suhani", "partner_name": "Aria", "event_id": wd.id, "seed": 1})
    svc.add_player(session, t, {"name": "Dina", "partner_name": "Eden", "event_id": wd.id, "seed": 2})
    svc.generate_draw(session, t, rng_seed=7, event_id=ws.id)
    svc.generate_draw(session, t, rng_seed=8, event_id=wd.id)
    session.commit()
    t = svc.load_tournament(session, t.id)
    ws_match = next(m for m in t.matches if m.event_id == ws.id and m.player1_id and m.player2_id)
    wd_match = next(m for m in t.matches if m.event_id == wd.id and m.player1_id and m.player2_id)
    svc.assign_court(session, t, ws_match, None, "09:00")
    with pytest.raises(svc.ConflictError) as caught:
        svc.assign_court(session, t, wd_match, None, "09:10")
    assert caught.value.conflicts
    match, warnings = svc.assign_court(session, t, wd_match, None, "09:10", force=True)
    assert match.scheduled_time.startswith("09:10")
    assert warnings
    # Enough rest later in the morning is allowed.
    svc.assign_court(session, t, wd_match, None, "10:00")
    session.commit()
    t = svc.load_tournament(session, t.id)
    wd_match = next(m for m in t.matches if m.id == wd_match.id)
    assert not svc.person_schedule_conflicts(t, wd_match)
