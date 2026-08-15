# Dolphin Badminton Academy — Tournament Manager

Pink-and-black tournament software for running badminton events from a phone, tablet, or laptop.

**Flow:** Create → Configure → Add players → Seed → Generate draw → Lock → Run matches → Enter scores → Winners advance → Print.

## Run locally

You need Python 3.11+. Same SQLite database approach as the school app.

```bash
cd dolphin-tournament
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python3 wsgi.py
```

Open [http://127.0.0.1:5000](http://127.0.0.1:5000). Public pages (draw, players, schedule, results) need no login. Admin management is at `/login` (default `admin` / `dolphin`, or set `ADMIN_USERNAME` and `ADMIN_PASSWORD`).

Data is stored in `data/tournament.db`.

```bash
python3 -m pytest -q
```

## Keep data on Render (Neon Postgres)

Render’s free web disk is wiped on every deploy and sleep. **Local stays SQLite** in `data/tournament.db`. Production must use Postgres via `DATABASE_URL`.

1. Create a free project at [console.neon.tech](https://console.neon.tech) → copy the connection string (`postgresql://...?sslmode=require`).
2. Open the existing Render service `dolphin-tournament` → **Environment**.
3. Add `DATABASE_URL` = that Neon string → **Save Changes** (Render redeploys).
4. In Render logs, confirm `[dolphin] database: postgres`. After that, tournament data survives deploys.

Also set `ADMIN_USERNAME` and `ADMIN_PASSWORD` so the public site cannot change draws. Local defaults are `admin` / `dolphin`.

If `DATABASE_URL` is missing, the app uses SQLite and the next deploy starts empty. Do not create a Render Postgres database (the free plan was discontinued).

Live site: [https://dolphin-tournament.onrender.com](https://dolphin-tournament.onrender.com). Free Render services sleep when idle; the first load after that can take ~30 seconds.

## Defaults (badminton)

Best of 3, 21 points, win by 2, 30-point cap. All of these are editable per tournament.

## What this version does

- Admin desk vs public tournament pages
- Several events in one tournament (WS, WD, XD, MS, MD, or custom)
- One person can enter several events; doubles entries are two people
- Player profile with match history and “my schedule”
- Single elimination with real seeding (seed 1 vs 2 on opposite halves)
- Automatic byes for non-power-of-two fields
- Automatic winner advancement
- Round robin (simple standings)
- Shared courts, estimated match times (labelled EXPECTED until confirmed), rest-time conflict checks
- Print layouts (A4), results
- CSV player import

## Not in this version

- Group stage → knockout (format is reserved)
- Spectator accounts / social features
- Editing a completed match (unlock and regenerate instead)
