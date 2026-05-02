# ThreatLens

ThreatLens is a self-hosted threat intelligence workspace for collecting RSS feeds, reading the full articles behind them, and triaging what matters.

It gives security teams a simple dashboard for feeds, alerts, tags, notes, read/starred state, and optional AI-assisted summaries and daily briefs.

## What It Does

- Pulls and stores RSS feeds
- Fetches and extracts readable article text
- Lets analysts mark items as read, starred, tagged, and noted
- Supports saved dashboard layouts with RSS, alert, notes, and brief panels
- Finds alert matches from keyword interests before and after saving them
- Provides role-based users: `admin`, `analyst`, and `viewer`
- Supports API tokens, audit logs, and feed import/export
- Sends optional webhook notifications with admin-controlled destinations
- Includes optional AI features for summaries, relevance scoring, task history, and daily briefs

## Quick Start

Copy the example environment file:

```bash
cp .env.example .env
```

Edit `.env` and set real values for at least:

```bash
POSTGRES_PASSWORD=
REDIS_PASSWORD=
DATABASE_URL=
REDIS_URL=
JWT_SECRET=
APP_DATA_ENCRYPTION_KEY=
ADMIN_EMAIL=
ADMIN_PASSWORD=
```

For a local HTTP-only test, also set:

```bash
APP_ENV=development
AUTH_COOKIE_SECURE=false
```

For the first startup, either set:

```bash
SEED_ADMIN_ON_STARTUP=true
```

or create the admin user after the stack is running:

```bash
docker compose exec api python -m app.scripts.seed_admin
```

Start everything:

```bash
docker compose up -d --build
```

Open:

```text
http://localhost:3000
```

Log in with the `ADMIN_EMAIL` and `ADMIN_PASSWORD` from your `.env`.

After the first admin account exists, set `SEED_ADMIN_ON_STARTUP=false` for normal use.

## Optional AI Features

AI is disabled by default.

To enable the AI workspace, set:

```bash
AI_ENABLED=true
```

Then open **Settings -> AI** in the app and configure an OpenAI-compatible endpoint, model, and API key if your provider needs one.

If your AI provider is on a private network, also set:

```bash
ALLOW_PRIVATE_NETWORK_AI=true
```

The AI features are optional. ThreatLens still works as a feed reader, alerting, tagging, and triage tool without them.

## Useful Commands

Check services:

```bash
docker compose ps
```

View logs:

```bash
docker compose logs -f api
docker compose logs -f worker
docker compose logs -f web
```

Run migrations:

```bash
docker compose exec api alembic upgrade head
```

Stop the stack:

```bash
docker compose down
```

Stop and remove the database/Redis volumes too:

```bash
docker compose down -v
```

## Local Development

Backend:

```bash
cd backend
python3 -m venv .venv
./.venv/bin/pip install -r requirements-dev.txt
./.venv/bin/alembic upgrade head
./.venv/bin/uvicorn app.main:app --reload
```

Worker and scheduler:

```bash
cd backend
./.venv/bin/celery -A app.tasks.celery_app.celery_app worker --loglevel=INFO
./.venv/bin/celery -A app.tasks.celery_app.celery_app beat --loglevel=INFO
```

Frontend:

```bash
cd web
npm ci
npm run dev
```

## Tests

Backend:

```bash
cd backend
./.venv/bin/python -m pytest
```

Frontend:

```bash
cd web
npm ci
npm test
npm run lint
npm run build
```

## Notes

- The default Docker setup runs PostgreSQL, Redis, the API, worker, scheduler, and web UI.
- The browser talks to the API through `/api/v1`.
- Feed/article fetching, AI calls, and webhook delivery can make outbound network requests.
- Private-network outbound access is off by default. Enable only what you trust in `.env`.
- Keep `APP_DATA_ENCRYPTION_KEY` safe. Some stored feed and webhook data depends on it.
- For detailed configuration, use `.env.example` as the reference.

## License

Apache-2.0. See [LICENSE](LICENSE).
