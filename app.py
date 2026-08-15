"""Dolphin Badminton Academy — Tournament Manager."""

from __future__ import annotations

import os
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, session as flask_session, url_for
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError

import db
import services as svc
from models import Event, Match, Person, Player

load_dotenv()


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ.get("SECRET_KEY") or "dolphin-dev-key"
    db.init_engine()

    admin_user = os.environ.get("ADMIN_USERNAME") or "admin"
    admin_password = os.environ.get("ADMIN_PASSWORD") or "dolphin"

    @app.teardown_appcontext
    def _close(_exc):
        pass

    def session_scope():
        return db.get_session()

    def json_error(message: str, code: int = 400, extra: dict | None = None):
        payload = {"ok": False, "error": message}
        if extra:
            payload.update(extra)
        return jsonify(payload), code

    def is_admin() -> bool:
        return bool(flask_session.get("admin"))

    def login_user() -> None:
        flask_session["admin"] = True

    def admin_required(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if is_admin():
                return fn(*args, **kwargs)
            if request.path.startswith("/api/"):
                return json_error("Admin login required.", 401)
            return redirect(url_for("login", next=request.path))

        return wrapper

    def api(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            session = session_scope()
            try:
                result = fn(session, *args, **kwargs)
                session.commit()
                return result
            except svc.ConflictError as exc:
                session.rollback()
                return json_error(str(exc), 409, {"conflicts": exc.conflicts})
            except svc.AppError as exc:
                session.rollback()
                return json_error(str(exc), 400)
            except IntegrityError:
                session.rollback()
                app.logger.exception("Database conflict")
                return json_error("That change conflicted with existing data. Check for a duplicate seed or player.", 409)
            except (OperationalError, SQLAlchemyError):
                session.rollback()
                app.logger.exception("Database error")
                return json_error("Database hiccup. Try that again.", 503)
            except Exception:
                session.rollback()
                app.logger.exception("Unhandled API error")
                return json_error("Something went wrong. Try that again.", 500)
            finally:
                session.close()

        return wrapper

    def event_arg():
        return request.args.get("event_id") or (request.get_json(silent=True) or {}).get("event_id")

    @app.get("/health")
    def health():
        return jsonify({"ok": True})

    @app.get("/login")
    def login():
        if is_admin():
            return redirect(request.args.get("next") or url_for("home"))
        return render_template("login.html", next=request.args.get("next") or "")

    @app.post("/login")
    def login_post():
        data = request.get_json(silent=True) or request.form
        username = (data.get("username") or "").strip()
        password = data.get("password") or ""
        nxt = data.get("next") or request.args.get("next") or ""
        if username == admin_user and password == admin_password:
            login_user()
            if request.is_json:
                return jsonify({"ok": True})
            return redirect(nxt if nxt.startswith("/") else url_for("home"))
        if request.is_json:
            return json_error("Invalid username or password.", 401)
        return render_template("login.html", error="Invalid username or password.", next=nxt), 401

    @app.post("/logout")
    @app.get("/logout")
    def logout():
        flask_session.clear()
        if request.is_json or request.path.startswith("/api/"):
            return jsonify({"ok": True})
        return redirect(url_for("home"))

    @app.get("/")
    def home():
        session = session_scope()
        try:
            tournaments = svc.list_tournaments(session)
        finally:
            session.close()
        return render_template("home.html", tournaments=tournaments, is_admin=is_admin())

    @app.get("/tournaments/new")
    @admin_required
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
        return render_template(
            "tournament.html",
            tournament=payload["tournament"],
            tid=tid,
            public=True,
            is_admin=is_admin(),
        )

    @app.get("/admin/tournaments/<int:tid>")
    @admin_required
    def admin_tournament(tid: int):
        session = session_scope()
        try:
            t = svc.load_tournament(session, tid)
            payload = svc.full_payload(t)
        except svc.AppError:
            session.close()
            return redirect(url_for("home"))
        finally:
            session.close()
        return render_template(
            "tournament.html",
            tournament=payload["tournament"],
            tid=tid,
            public=False,
            is_admin=True,
        )

    @app.get("/tournaments/<int:tid>/players/<int:pid>")
    def player_page(tid: int, pid: int):
        session = session_scope()
        try:
            t = svc.load_tournament(session, tid)
            player = session.get(Player, pid)
            if not player or player.tournament_id != tid:
                return redirect(url_for("tournament_page", tid=tid))
            people = svc._entry_people(player)
            if len(people) == 1:
                return redirect(url_for("person_page", tid=tid, pid=people[0].id))
            payload = svc.player_page(t, player)
            tournament_name = t.name
        finally:
            session.close()
        return render_template("player.html", tid=tid, payload=payload, tournament_name=tournament_name)

    @app.get("/tournaments/<int:tid>/people/<int:pid>")
    def person_page(tid: int, pid: int):
        session = session_scope()
        try:
            t = svc.load_tournament(session, tid)
            person = session.get(Person, pid)
            if not person or person.tournament_id != tid:
                return redirect(url_for("tournament_page", tid=tid))
            payload = svc.person_page(t, person)
            tournament_name = t.name
        finally:
            session.close()
        return render_template("person.html", tid=tid, payload=payload, tournament_name=tournament_name, is_admin=is_admin())

    @app.get("/print/<int:tid>/<kind>")
    def print_page(tid: int, kind: str):
        session = session_scope()
        try:
            t = svc.load_tournament(session, tid)
            payload = svc.full_payload(t, request.args.get("event_id"))
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

    @app.get("/api/me")
    def api_me():
        return jsonify({"ok": True, "admin": is_admin()})

    @app.get("/api/tournaments")
    @api
    def api_list(session):
        return jsonify({"ok": True, "tournaments": svc.list_tournaments(session)})

    @app.post("/api/tournaments")
    @admin_required
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
        return jsonify({"ok": True, "admin": is_admin(), **svc.full_payload(t, event_arg())})

    @app.patch("/api/tournaments/<int:tid>")
    @admin_required
    @api
    def api_update(session, tid: int):
        t = svc.load_tournament(session, tid)
        svc.update_tournament(session, t, request.get_json(force=True) or {})
        session.flush()
        return jsonify({"ok": True, **svc.full_payload(t, event_arg())})

    @app.post("/api/tournaments/<int:tid>/events")
    @admin_required
    @api
    def api_add_event(session, tid: int):
        t = svc.load_tournament(session, tid)
        event = svc.create_event(session, t, request.get_json(force=True) or {})
        session.flush()
        return jsonify({"ok": True, "event_id": event.id, **svc.full_payload(t, event.id)})

    @app.patch("/api/tournaments/<int:tid>/events/<int:eid>")
    @admin_required
    @api
    def api_edit_event(session, tid: int, eid: int):
        t = svc.load_tournament(session, tid)
        event = session.get(Event, eid)
        if not event or event.tournament_id != tid:
            return json_error("Event not found.", 404)
        svc.update_event(session, t, event, request.get_json(force=True) or {})
        session.flush()
        return jsonify({"ok": True, **svc.full_payload(t, eid)})

    @app.delete("/api/tournaments/<int:tid>/events/<int:eid>")
    @admin_required
    @api
    def api_delete_event(session, tid: int, eid: int):
        t = svc.load_tournament(session, tid)
        event = session.get(Event, eid)
        if not event or event.tournament_id != tid:
            return json_error("Event not found.", 404)
        svc.delete_event(session, t, event)
        session.flush()
        return jsonify({"ok": True, **svc.full_payload(t)})

    @app.post("/api/tournaments/<int:tid>/players")
    @admin_required
    @api
    def api_add_player(session, tid: int):
        t = svc.load_tournament(session, tid)
        body = request.get_json(force=True) or {}
        event_id = body.get("event_id")
        if body.get("bulk"):
            names = body.get("names") or []
            if isinstance(names, str):
                names = names.splitlines()
            svc.add_players_bulk(session, t, names, event_id=event_id)
        elif body.get("csv"):
            svc.import_players_csv(session, t, body["csv"], event_id=event_id)
        else:
            svc.add_player(session, t, body)
        session.flush()
        return jsonify({"ok": True, **svc.full_payload(t, event_id)})

    @app.patch("/api/tournaments/<int:tid>/players/<int:pid>")
    @admin_required
    @api
    def api_edit_player(session, tid: int, pid: int):
        t = svc.load_tournament(session, tid)
        player = session.get(Player, pid)
        if not player or player.tournament_id != tid:
            return json_error("Player not found.", 404)
        svc.update_player(session, t, player, request.get_json(force=True) or {})
        session.flush()
        return jsonify({"ok": True, **svc.full_payload(t, player.event_id)})

    @app.delete("/api/tournaments/<int:tid>/players/<int:pid>")
    @admin_required
    @api
    def api_delete_player(session, tid: int, pid: int):
        t = svc.load_tournament(session, tid)
        player = session.get(Player, pid)
        if not player or player.tournament_id != tid:
            return json_error("Player not found.", 404)
        event_id = player.event_id
        svc.delete_player(session, t, player)
        session.flush()
        return jsonify({"ok": True, **svc.full_payload(t, event_id)})

    @app.post("/api/tournaments/<int:tid>/seed-by-ranking")
    @admin_required
    @api
    def api_seed_rank(session, tid: int):
        t = svc.load_tournament(session, tid)
        body = request.get_json(silent=True) or {}
        svc.assign_seeds_by_ranking(session, t, body.get("event_id") or event_arg())
        session.flush()
        return jsonify({"ok": True, **svc.full_payload(t, body.get("event_id") or event_arg())})

    @app.post("/api/tournaments/<int:tid>/draw")
    @admin_required
    @api
    def api_draw(session, tid: int):
        t = svc.load_tournament(session, tid)
        body = request.get_json(silent=True) or {}
        event_id = body.get("event_id") or event_arg()
        svc.generate_draw(session, t, confirm=bool(body.get("confirm")), event_id=event_id)
        session.flush()
        return jsonify({"ok": True, **svc.full_payload(t, event_id)})

    @app.post("/api/tournaments/<int:tid>/draw/lock")
    @admin_required
    @api
    def api_lock(session, tid: int):
        t = svc.load_tournament(session, tid)
        body = request.get_json(silent=True) or {}
        event_id = body.get("event_id") or event_arg()
        svc.lock_draw(session, t, event_id=event_id)
        session.flush()
        return jsonify({"ok": True, **svc.full_payload(t, event_id)})

    @app.post("/api/tournaments/<int:tid>/draw/unlock")
    @admin_required
    @api
    def api_unlock(session, tid: int):
        t = svc.load_tournament(session, tid)
        body = request.get_json(silent=True) or {}
        event_id = body.get("event_id") or event_arg()
        svc.unlock_draw(session, t, confirm=bool(body.get("confirm")), event_id=event_id)
        session.flush()
        return jsonify({"ok": True, **svc.full_payload(t, event_id)})

    @app.post("/api/tournaments/<int:tid>/start")
    @admin_required
    @api
    def api_start(session, tid: int):
        t = svc.load_tournament(session, tid)
        body = request.get_json(silent=True) or {}
        event_id = body.get("event_id") or event_arg()
        svc.start_tournament(session, t, event_id=event_id)
        session.flush()
        return jsonify({"ok": True, **svc.full_payload(t, event_id)})

    @app.post("/api/tournaments/<int:tid>/courts/auto")
    @admin_required
    @api
    def api_auto_courts(session, tid: int):
        t = svc.load_tournament(session, tid)
        svc.refresh_courts_and_schedule(session, t, assign=True, times=True)
        session.flush()
        return jsonify({"ok": True, **svc.full_payload(t, event_arg())})

    @app.post("/api/matches/<int:mid>/start")
    @admin_required
    @api
    def api_match_start(session, mid: int):
        match = session.get(Match, mid)
        if not match:
            return json_error("Match not found.", 404)
        t = svc.load_tournament(session, match.tournament_id)
        svc.start_match(session, t, match)
        session.flush()
        return jsonify({"ok": True, **svc.full_payload(t, match.event_id)})

    @app.post("/api/matches/<int:mid>/result")
    @admin_required
    @api
    def api_result(session, mid: int):
        match = session.get(Match, mid)
        if not match:
            return json_error("Match not found.", 404)
        t = svc.load_tournament(session, match.tournament_id)
        svc.enter_result(session, t, match, request.get_json(force=True) or {})
        session.flush()
        return jsonify({"ok": True, **svc.full_payload(t, match.event_id)})

    @app.patch("/api/matches/<int:mid>")
    @admin_required
    @api
    def api_match_patch(session, mid: int):
        match = session.get(Match, mid)
        if not match:
            return json_error("Match not found.", 404)
        t = svc.load_tournament(session, match.tournament_id)
        body = request.get_json(force=True) or {}
        _, warnings = svc.assign_court(
            session,
            t,
            match,
            body.get("court_id"),
            body.get("scheduled_time"),
            force=bool(body.get("force")),
            time_locked=body.get("time_locked"),
        )
        session.flush()
        return jsonify({"ok": True, "warnings": warnings, **svc.full_payload(t, match.event_id)})

    return app


app = create_app()
