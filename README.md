# ThreatLens

ThreatLens is a self-hosted app for tracking security (or any other) RSS feeds and articles.

It stores feeds, extracts article text, and gives a single pane of glass to review articles, alerting matches, optional AI summaries/recommendations. 

## Screenshots

![ThreatLens dashboard showing RSS triage and a daily brief panel](image.png)

| Original article preview | RSS-only triage view |
|---|---|
| ![ThreatLens original article preview drawer opened from an RSS item](image-1.png) | ![ThreatLens RSS-only dashboard layout with filters and item triage controls](image-2.png) |

## Features

- RSS feed collection and article extraction
- Read/starred state, notes, tags, and saved dashboard views
- Keyword alert interests with preview before saving
- Feed import/export and webhook notifications
- Role-based users: `admin`, `analyst`, and `viewer`
- API tokens and audit logs
- Optional AI summaries, relevance scoring, task history, and daily briefs

## Quick Start

Create a local environment file with fresh random secrets:

```bash
./scripts/create-env.sh
```

The script prints the generated admin login. To choose your own admin identity, run:

```bash
ADMIN_EMAIL=you@example.com ADMIN_PASSWORD='use-a-long-password' ./scripts/create-env.sh
```

For a production or internet-facing deployment, review `.env.example` and replace any local-only settings before first startup.

Pull the published images and start everything:

```bash
docker compose pull
docker compose up -d
```

Or build the images locally from source:

```bash
docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build
```

Open the app:

```text
http://localhost:3000
```

Log in with `ADMIN_EMAIL` and `ADMIN_PASSWORD`.

After the first admin account exists, set `SEED_ADMIN_ON_STARTUP=false` for normal use.

## AI

AI is disabled by default.

To enable it, set:

```bash
AI_ENABLED=true
```

Then open **Settings -> AI** and configure an OpenAI-compatible endpoint, model, and API key if needed.

If your AI provider is on a private network, also set:

```bash
ALLOW_PRIVATE_NETWORK_AI=true
```

ThreatLens works without AI.

## Useful Commands

Update to the latest published images:

```bash
docker compose pull
docker compose up -d
```

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
- Use `.env.example` as the configuration reference.

## License

Apache-2.0. See [LICENSE](LICENSE).
