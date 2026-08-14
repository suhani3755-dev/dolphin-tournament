from __future__ import annotations

import db
from app import create_app


def test_api_eight_player_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "api.db"))
    db.ENGINE = None
    db.SessionLocal = None
    app = create_app()
    client = app.test_client()

    created = client.post("/api/tournaments", json={"name": "API Open", "sport": "badminton"})
    assert created.json["ok"]
    tid = created.json["tournament"]["id"]

    patched = client.patch(
        f"/api/tournaments/{tid}",
        json={"format": "single_elimination", "best_of": 3, "points_per_game": 21, "num_courts": 2},
    )
    assert patched.json["ok"]

    for i, name in enumerate(["Aria", "Bryn", "Cora", "Dina", "Eden", "Faye", "Gia", "Hana"], start=1):
        body = {"name": name, "club": "Dolphin"}
        if i <= 4:
            body["seed"] = i
        added = client.post(f"/api/tournaments/{tid}/players", json=body)
        assert added.json["ok"], added.json

    draw = client.post(f"/api/tournaments/{tid}/draw", json={})
    assert draw.json["ok"]
    matches = draw.json["matches"]
    first_round = [m for m in matches if m["round_index"] == 0]
    seeds = {}
    for m in first_round:
        for p in (m["player1"], m["player2"]):
            if p and p["seed"]:
                seeds[p["seed"]] = m["match_number"]
    assert 1 in seeds and 2 in seeds
    # seed 1 and 2 should not share a first-round match
    assert seeds[1] != seeds[2]

    assert client.post(f"/api/tournaments/{tid}/draw/lock", json={}).json["ok"]
    assert client.post(f"/api/tournaments/{tid}/start", json={}).json["ok"]

    started = client.get(f"/api/tournaments/{tid}").json
    ready = [m for m in started["matches"] if m["status"] == "ready"]
    assigned = [m for m in ready if m["court_id"]]
    assert len(assigned) == 2
    assert all(m["court"] and m["court"]["name"] for m in assigned)
    assert all(m["scheduled_time"] for m in ready)
    assert len(started["waiting"]) == 2

    for _ in range(8):
        payload = client.get(f"/api/tournaments/{tid}").json
        ready = [m for m in payload["matches"] if m["status"] in {"ready", "live"} and m["player1"] and m["player2"]]
        if not ready:
            break
        match = ready[0]
        res = client.post(
            f"/api/matches/{match['id']}/result",
            json={"result_type": "normal", "scores": [[21, 11], [21, 9]]},
        )
        assert res.json["ok"], res.json

    done = client.get(f"/api/tournaments/{tid}").json
    assert done["tournament"]["status"] == "completed"
    assert done["results"]["champion"]
