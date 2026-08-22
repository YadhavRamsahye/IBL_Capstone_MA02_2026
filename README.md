# IBL Group - Traffic Bottleneck Detection System

Real-time traffic monitoring dashboard for Mauritius, built for IBL Group as a Curtin Mauritius Capstone Computing Project. The system pulls live HLS camera feeds from the MYT Traffic Watch network, runs YOLOv8 vehicle detection on each stream, classifies congestion severity, detects incidents (sudden congestion, road blockages, rapid buildup), and surfaces it all on a live map dashboard with AI-generated traffic summaries.

## Tech stack

- **Backend:** FastAPI + Uvicorn (ASGI), Jinja2 templates
- **Detection:** Ultralytics YOLOv8 + OpenCV, with an FFmpeg reconnect-per-frame pipeline for HLS streams
- **Database:** PostgreSQL via SQLAlchemy (async) + asyncpg; bcrypt for password hashing
- **AI summaries:** Anthropic Claude or Google Gemini (`SUMMARY_PROVIDER` env var), with a template-based fallback that requires no API key
- **Auth:** Session-based login (`itsdangerous`/Starlette `SessionMiddleware`), backed by a PostgreSQL `users` table

## Prerequisites

- Python 3.10+ (verified working on 3.14.6; also compatible with 3.12)
- [FFmpeg and ffprobe](https://www.gyan.dev/ffmpeg/builds/) on PATH - required for HLS stream validation and frame grabs
- PostgreSQL 17 (`winget install PostgreSQL.PostgreSQL.17` on Windows)

## Setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt

copy .env.example .env
# then fill in ANTHROPIC_API_KEY (or GEMINI_API_KEY) and the DB_* values

python run_schema.py    # creates the database, enums and tables
python seed_users.py    # seeds the initial accounts
python check_db.py      # verifies the schema and seed data
```

## Running

```powershell
python main.py
```

Serves the dashboard at `http://127.0.0.1:8000`. Without PostgreSQL or an AI provider key configured, the app degrades gracefully - it starts on mock camera data and falls back to template-based traffic summaries rather than failing to start.

## Project structure

- `main.py` - FastAPI app, routes, camera task orchestration, WebSocket updates
- `auth.py` - login/signup wired to PostgreSQL with bcrypt, plus a logged demo-login fallback for when the database is unreachable
- `database.py` - async SQLAlchemy engine/session setup with graceful degradation if Postgres is offline
- `detection/` - camera catalogue, HLS/mock/real pipelines, YOLOv8 inference, incident detection, AI summary providers
- `tools/` - calibration and diagnostic scripts (capacity, direction, camera discovery, Gemini connectivity)
- `templates/` - dashboard, map, analytics, login/signup pages
- `TECHNICAL_BRIEFING.md` - detailed architecture and failure-mode reference

## Team

IBL Group - Curtin Mauritius Capstone Computing Project (ISAD3000/ISAD3001)

- Sahil Singh Rughoo (22414560) - Technical Lead
- Yadhav Sharma Ramsahye (22108355) - Developer
- Mokshan Mehess (22703417) - Document Lead
