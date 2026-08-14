"""Dolphin Badminton Academy — Tournament Manager."""

from __future__ import annotations

import os
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, url_for

import db
import services as svc
from models import Match, Player

load_dotenv()


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ.get("SECRET_KEY") or "dolphin-dev-key"
    db.init_engine()

    @app.teardown_appcontext
    def _close(_exc):
        pass

    def session_scope():
        return db.get_session()

    def json_error(message: str, code: int = 400):
        return jsonify({"ok": False, "error": message}), code

    def api(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            session = session_scope()
            try:
                result = fn(session, *args, **kwargs)
                session.commit()
                return result
            except svc.AppError as exc:
                session.rollback()
                return json_error(str(exc), 400)
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

        return wrapper

    @app.get("/health")
    def health():
        return jsonify({"ok": True})

    @app.get("/")
    def home():
        session = session_scope()
        try:
            tournaments = svc.list_tournaments(session)
        finally:
            session.close()
        return render_template("home.html", tournaments=tournaments)

    @app.get("/tournaments/new")
    def new_tournament():
        return render_template("new.html")

    @app.get("/tournaments/<int:tid>")
    def tournament_page(tid: int):
        session = session_scope()
        try:
            t = svc.load_tournament(session, tid)
            payload = svc.full_payload(t)
        except svc.AppError:
            session.close()
            return redirect(url_for("home"))
        finally:
            session.close()
        return render_template("tournament.html", tournament=payload["tournament"], tid=tid)

    @app.get("/tournaments/<int:tid>/players/<int:pid>")
    def player_page(tid: int, pid: int):
        session = session_scope()
        try:
            t = svc.load_tournament(session, tid)
            player = session.get(Player, pid)
            if not player or player.tournament_id != tid:
                return redirect(url_for("tournament_page", tid=tid))
            payload = svc.player_page(t, player)
        finally:
            session.close()
        return render_template("player.html", tid=tid, payload=payload, tournament_name=t.name)

    @app.get("/print/<int:tid>/<kind>")
    def print_page(tid: int, kind: str):
        session = session_scope()
        try:
            t = svc.load_tournament(session, tid)
            payload = svc.full_payload(t)
            match = None
            mid = request.args.get("match", type=int)
            if mid:
                match = next((m for m in payload["matches"] if m["id"] == mid), None)
        finally:
            session.close()
        allowed = {"draw", "empty", "final", "schedule", "players", "results", "sheet"}
        if kind not in allowed:
            return redirect(url_for("tournament_page", tid=tid))
        template = "print/sheet.html" if kind == "sheet" else "print/document.html"
        return render_template(
            template,
            kind=kind,
            t=payload["tournament"],
            matches=payload["matches"],
            results=payload["results"],
            match=match,
            preview=payload["preview"],
        )

    @app.get("/api/tournaments")
    @api
    def api_list(session):
        return jsonify({"ok": True, "tournaments": svc.list_tournaments(session)})

    @app.post("/api/tournaments")
    @api
    def api_create(session):
        t = svc.create_tournament(session, request.get_json(force=True) or {})
        session.commit()
        t = svc.load_tournament(session, t.id)
        return jsonify({"ok": True, "tournament": svc.tournament_to_dict(t, True)})

    @app.get("/api/tournaments/<int:tid>")
    @api
    def api_get(session, tid: int):
        t = svc.load_tournament(session, tid)
        if t.matches:
            svc.refresh_courts_and_schedule(session, t)
            session.flush()
            t = svc.load_tournament(session, tid)
        return jsonify({"ok": True, **svc.full_payload(t)})

    @app.patch("/api/tournaments/<int:tid>")
    @api
    def api_update(session, tid: int):
        t = svc.load_tournament(session, tid)
        svc.update_tournament(session, t, request.get_json(force=True) or {})
        session.flush()
        t = svc.load_tournament(session, tid)
        return jsonify({"ok": True, **svc.full_payload(t)})

    @app.post("/api/tournaments/<int:tid>/players")
    @api
    def api_add_player(session, tid: int):
        t = svc.load_tournament(session, tid)
        body = request.get_json(force=True) or {}
        if body.get("bulk"):
            names = body.get("names") or []
            if isinstance(names, str):
                names = names.splitlines()
            svc.add_players_bulk(session, t, names)
        elif body.get("csv"):
            svc.import_players_csv(session, t, body["csv"])
        else:
            svc.add_player(session, t, body)
        session.flush()
        t = svc.load_tournament(session, tid)
        return jsonify({"ok": True, **svc.full_payload(t)})

    @app.patch("/api/tournaments/<int:tid>/players/<int:pid>")
    @api
    def api_edit_player(session, tid: int, pid: int):
        t = svc.load_tournament(session, tid)
        player = session.get(Player, pid)
        if not player or player.tournament_id != tid:
            return json_error("Player not found.", 404)
        svc.update_player(session, t, player, request.get_json(force=True) or {})
        session.flush()
        t = svc.load_tournament(session, tid)
        return jsonify({"ok": True, **svc.full_payload(t)})

    @app.delete("/api/tournaments/<int:tid>/players/<int:pid>")
    @api
    def api_delete_player(session, tid: int, pid: int):
        t = svc.load_tournament(session, tid)
        player = session.get(Player, pid)
        if not player or player.tournament_id != tid:
            return json_error("Player not found.", 404)
        svc.delete_player(session, t, player)
        session.flush()
        t = svc.load_tournament(session, tid)
        return jsonify({"ok": True, **svc.full_payload(t)})

    @app.post("/api/tournaments/<int:tid>/seed-by-ranking")
    @api
    def api_seed_rank(session, tid: int):
        t = svc.load_tournament(session, tid)
        svc.assign_seeds_by_ranking(session, t)
        session.flush()
        t = svc.load_tournament(session, tid)
        return jsonify({"ok": True, **svc.full_payload(t)})

    @app.post("/api/tournaments/<int:tid>/draw")
    @api
    def api_draw(session, tid: int):
        t = svc.load_tournament(session, tid)
        body = request.get_json(silent=True) or {}
        svc.generate_draw(session, t, confirm=bool(body.get("confirm")))
        session.flush()
        t = svc.load_tournament(session, tid)
        return jsonify({"ok": True, **svc.full_payload(t)})

    @app.post("/api/tournaments/<int:tid>/draw/lock")
    @api
    def api_lock(session, tid: int):
        t = svc.load_tournament(session, tid)
        svc.lock_draw(session, t)
        session.flush()
        t = svc.load_tournament(session, tid)
        return jsonify({"ok": True, **svc.full_payload(t)})

    @app.post("/api/tournaments/<int:tid>/draw/unlock")
    @api
    def api_unlock(session, tid: int):
        t = svc.load_tournament(session, tid)
        body = request.get_json(silent=True) or {}
        svc.unlock_draw(session, t, confirm=bool(body.get("confirm")))
        session.flush()
        t = svc.load_tournament(session, tid)
        return jsonify({"ok": True, **svc.full_payload(t)})

    @app.post("/api/tournaments/<int:tid>/start")
    @api
    def api_start(session, tid: int):
        t = svc.load_tournament(session, tid)
        svc.start_tournament(session, t)
        session.flush()
        t = svc.load_tournament(session, tid)
        return jsonify({"ok": True, **svc.full_payload(t)})

    @app.post("/api/tournaments/<int:tid>/courts/auto")
    @api
    def api_auto_courts(session, tid: int):
        t = svc.load_tournament(session, tid)
        svc.refresh_courts_and_schedule(session, t, assign=True, times=True)
        session.flush()
        t = svc.load_tournament(session, tid)
        return jsonify({"ok": True, **svc.full_payload(t)})

    @app.post("/api/matches/<int:mid>/start")
    @api
    def api_match_start(session, mid: int):
        match = session.get(Match, mid)
        if not match:
            return json_error("Match not found.", 404)
        t = svc.load_tournament(session, match.tournament_id)
        svc.start_match(session, t, match)
        session.flush()
        t = svc.load_tournament(session, t.id)
        return jsonify({"ok": True, **svc.full_payload(t)})

    @app.post("/api/matches/<int:mid>/result")
    @api
    def api_result(session, mid: int):
        match = session.get(Match, mid)
        if not match:
            return json_error("Match not found.", 404)
        t = svc.load_tournament(session, match.tournament_id)
        svc.enter_result(session, t, match, request.get_json(force=True) or {})
        session.flush()
        t = svc.load_tournament(session, t.id)
        return jsonify({"ok": True, **svc.full_payload(t)})

    @app.patch("/api/matches/<int:mid>")
    @api
    def api_match_patch(session, mid: int):
        match = session.get(Match, mid)
        if not match:
            return json_error("Match not found.", 404)
        t = svc.load_tournament(session, match.tournament_id)
        body = request.get_json(force=True) or {}
        svc.assign_court(session, t, match, body.get("court_id"), body.get("scheduled_time"))
        session.flush()
        t = svc.load_tournament(session, t.id)
        return jsonify({"ok": True, **svc.full_payload(t)})

    return app


app = create_app()
