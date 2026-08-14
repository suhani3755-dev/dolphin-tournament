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

Open [http://127.0.0.1:5000](http://127.0.0.1:5000).

Data is stored in `data/tournament.db`.

```bash
python3 -m pytest -q
```

## Host for free on Render

Render’s free web service does not keep files, so SQLite would reset. Use a free **Neon** Postgres database (the hosted database), and keep SQLite only for your laptop.

1. Push this folder to a GitHub repository.
2. Create a free project at [neon.tech](https://neon.tech) → copy the connection string (`postgresql://...`).
3. On [render.com](https://render.com) → **New → Web Service** → connect the GitHub repo.
4. Settings:
   - **Build:** `pip install -r requirements.txt`
   - **Start:** `gunicorn wsgi:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120`
   - **Environment:**
     - `DATABASE_URL` = your Neon URL
     - `SECRET_KEY` = any long random string
5. Deploy. Render gives you a `*.onrender.com` URL that works on any device.

The first visit creates the tables automatically.

Free Render services sleep after idle time; the first load after that can take ~30 seconds.

## Defaults (badminton)

Best of 3, 21 points, win by 2, 30-point cap. All of these are editable per tournament.

## What this version does

- Single elimination with real seeding (seed 1 vs 2 on opposite halves)
- Automatic byes for non-power-of-two fields
- Automatic winner advancement
- Round robin (simple standings)
- Courts, print layouts (A4), results, player pages
- CSV player import

## Not in this version

- Group stage → knockout (format is reserved)
- Logins / spectator accounts
- Editing a completed match (unlock and regenerate instead)
