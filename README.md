# ThreatLens

ThreatLens is a self-hosted cyber threat intel feed aggregator built for homelab use.

## Stack

- Backend: FastAPI + SQLAlchemy + Alembic + PostgreSQL
- Jobs: Celery worker + Celery Beat + Redis
- Extraction: `trafilatura` with `readability-lxml` fallback
- Frontend: React + TypeScript + Vite + React Query + React Router + Tailwind
- Deployment: Docker Compose (`web`, `api`, `worker`, `beat`, `db`, `redis`)

## Features Implemented

- JWT auth (`/auth/register`, `/auth/login`, `/auth/me`)
- Feed CRUD + manual refresh
- Scheduled feed polling with conditional GET (`ETag`, `Last-Modified`)
- Normalized item ingestion with dedupe and content hash update detection
- Async article dereference and text extraction with fallback strategy
- Per-user triage state (read/star/note)
- Tags + item tag assignment
- Saved views API (MVP-lite)
- React dashboard for triage + feed management UI

## Run with Docker Compose

1. Create env file:

```bash
cp .env.example .env
```

2. Start all services:

```bash
docker compose up --build
```

3. Open:

- Web UI: `http://localhost:3000`
- API docs: `http://localhost:8000/docs`

## Local Dev

### Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Worker:

```bash
cd backend
celery -A app.tasks.celery_app.celery_app worker --loglevel=INFO
```

Beat:

```bash
cd backend
celery -A app.tasks.celery_app.celery_app beat --loglevel=INFO
```

### Frontend

```bash
cd web
npm install
npm run dev
```

## Notes

- Admin user is seeded on API startup from `ADMIN_EMAIL` / `ADMIN_PASSWORD`.
- Feed scheduler checks due feeds every 60 seconds and enqueues per-feed jobs.
- OpenAI enrichment is not implemented yet by design (future async stage).
