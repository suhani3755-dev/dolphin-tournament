from __future__ import annotations

import db
from app import create_app


def _login(client):
    res = client.post("/login", json={"username": "admin", "password": "dolphin"})
    assert res.status_code == 200, res.json
    assert res.json["ok"]


def test_api_requires_admin_for_create(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "auth.db"))
    db.ENGINE = None
    db.SessionLocal = None
    app = create_app()
    client = app.test_client()
    denied = client.post("/api/tournaments", json={"name": "Nope"})
    assert denied.status_code == 401
    public = client.get("/api/tournaments")
    assert public.status_code == 200
    assert public.json["ok"]


def test_api_eight_player_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "api.db"))
    db.ENGINE = None
    db.SessionLocal = None
    app = create_app()
    client = app.test_client()
    _login(client)

    created = client.post("/api/tournaments", json={"name": "API Open", "sport": "badminton"})
    assert created.json["ok"]
    tid = created.json["tournament"]["id"]
    assert created.json["tournament"]["events"]

    admin_page = client.get(f"/admin/tournaments/{tid}")
    assert admin_page.status_code == 200
    assert b"static/js/app.js" in admin_page.data
    opened = client.get(f"/api/tournaments/{tid}")
    assert opened.status_code == 200
    assert opened.json["ok"]
    assert opened.json["tournament"]["id"] == tid

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
    assert seeds[1] != seeds[2]

    assert client.post(f"/api/tournaments/{tid}/draw/lock", json={}).json["ok"]
    assert client.post(f"/api/tournaments/{tid}/start", json={}).json["ok"]

    started = client.get(f"/api/tournaments/{tid}").json
    ready = [m for m in started["matches"] if m["status"] == "ready"]
    assigned = [m for m in ready if m["court_id"]]
    assert len(assigned) == 2
    assert all(m["court"] and m["court"]["name"] for m in assigned)
    assert all(m["scheduled_time"] for m in ready)
    assert all(m["time_label"] == "EXPECTED" for m in ready)
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
