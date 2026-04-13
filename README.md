# ThreatLens

ThreatLens is a self-hosted threat intelligence aggregator built for security teams. It pulls in feeds, processes articles, and gives analysts a clean interface to triage and track what matters.

## Stack Overview

The project is split into a few core services:

- `web` - React + TypeScript frontend
- `api` - FastAPI backend
- `worker` - Celery worker
- `beat` - Celery beat scheduler
- `db` - PostgreSQL
- `redis` - queue + coordination layer

## Features

- Multi-user support with role-based access control (`admin`, `analyst`, `viewer`)
- JWT authentication, browser session cookies, and personal API tokens
- Audit logging for security-relevant actions
- Multi-window dashboard with RSS, alerts, and notes panes
- Optional AI workspace for admin-managed endpoint configuration, task operations, usage stats, and prompt/profile tuning
- AI item enrichment with per-article summary + relevance scoring, plus a dashboard Daily Brief widget
- Per-user triage state (read, starred, notes, tags) and saved dashboard views
- Alert interests with live preview before save
- Personal webhook notifications with template variables, multi-event delivery history, analytics, retry, and test-send support
- Admin tagging controls with custom rules, preview, and background reapply
- Feed scheduling, metadata detection, import/export, and manual refresh controls
- Article fetching, readable content extraction, classification, and IOC extraction
- Stats dashboards for feed health, activity, domains, and signal distribution

## Setup

Copy the environment template:

```bash
cp .env.example .env
```

When running in Portainer (or any orchestrator that injects environment variables directly), a `.env` file is not required. The API reads process environment variables first; `.env` is only a local convenience.

You'll need to configure at least:

- `DATABASE_URL`
- `REDIS_URL`
- `JWT_SECRET`
- `ADMIN_EMAIL`
- `ADMIN_PASSWORD`

Admin bootstrap behavior:

- `SEED_ADMIN_ON_STARTUP=true` seeds `ADMIN_EMAIL`/`ADMIN_PASSWORD` on first startup.
- `SEED_ADMIN_RESET_PASSWORD_ON_STARTUP=true` forces that account password to be reset at startup (useful during controlled credential rotation).
- After bootstrap, set `SEED_ADMIN_RESET_PASSWORD_ON_STARTUP=false` unless you intentionally want startup-driven password resets.

There are a number of additional flags for auth hardening, rate limiting, and feed handling - check `.env.example` for full details.

## Running with Docker

Start everything:

```bash
docker compose up --build -d
```

Startup flow for `docker-compose.yml`:

- `api` runs `alembic upgrade head` on startup before serving requests.
- `worker` waits for the API health check, which indirectly gates on DB, Redis, and completed migrations.
- `beat` runs as its own container so periodic jobs do not multiply with worker replicas.
- `api` also handles runtime admin seeding via `SEED_ADMIN_*` flags.

For horizontally scaled production, treat startup migrations and admin seeding as bootstrap actions rather than steady-state behavior. Keep `RUN_MIGRATIONS_ON_STARTUP` and `SEED_ADMIN_ON_STARTUP` enabled only for the bootstrap instance or one-off deploy job, then disable them on long-lived replicas.

Check containers:

```bash
docker compose ps
```

Endpoints:

- UI: `http://localhost:3000`
- API health: `http://localhost:8000/health`
- Readiness: `http://localhost:8000/health/ready`
- Worker: `http://localhost:8000/health/worker`
- Beat: `http://localhost:8000/health/beat`
- Interactive API docs: `http://localhost:8000/docs`

Stop:

```bash
docker compose down
```

Remove volumes too:

```bash
docker compose down -v
```

## Common Operations

### Run database migrations

```bash
docker compose exec api alembic upgrade head
```

### Create or reset admin user

```bash
docker compose exec api python -m app.scripts.seed_admin
```

### Deployment troubleshooting (auth and startup)

1. Verify the configured admin account exists:

```bash
docker compose exec api python - <<'PY'
from sqlalchemy import select
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.user import User

s = get_settings()
db = SessionLocal()
u = db.scalar(select(User).where(User.email == s.admin_email.lower()))
print("admin_email:", s.admin_email, "exists:", bool(u))
db.close()
PY
```

2. If admin is missing at startup, ensure these values are explicitly set in your deployment bootstrap:
   - `SEED_ADMIN_ON_STARTUP=true`
   - `ADMIN_EMAIL=<value>`
   - `ADMIN_PASSWORD=<value>`
   - `RUN_MIGRATIONS_ON_STARTUP=true`
   - Disable those flags again on steady-state replicas once bootstrap is complete.

3. If lockout errors persist after stack recreation, clear Redis auth lock keys (or remove Redis volume):

```bash
docker compose exec redis sh -lc \
  "redis-cli --scan --pattern 'threatlens:auth:*' | xargs -r redis-cli del"
```

4. If deployed behind a reverse proxy, set `TRUSTED_PROXY_CIDRS` to the proxy network so IP-based auth throttling uses the real client IP from `X-Forwarded-For`.

### Logs

```bash
docker compose logs -f api
docker compose logs -f worker
docker compose logs -f beat
```

### Trigger feed refresh

```bash
curl -X POST http://localhost:8000/feeds/<feed_id>/refresh \
  -H "Authorization: Bearer <jwt>"
```

## Local Validation

- Backend test suite:

```bash
./backend/.venv/bin/pytest backend/tests -q
```

- Frontend production build:

```bash
# Uses `/api` as the default production API base. Override `VITE_API_BASE_URL` only for non-proxied deployments.
docker build -q -f web/Dockerfile web
```

- Runtime smoke:

```bash
docker compose up -d --build api worker beat web
curl http://localhost:8000/health/ready
curl http://localhost:3000/api/health/ready
```

## API Examples

Log in and capture a JWT:

```bash
TOKEN=$(curl -sS http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"admin123"}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
```

Preview an alert before saving it:

```bash
curl -X POST http://localhost:8000/alerts/preview \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Microsoft Preview",
    "category": "vendor",
    "keywords": ["microsoft", "exchange", "entra id"],
    "limit": 5
  }'
```

Create a webhook notification for new RSS items:

```bash
curl -X POST http://localhost:8000/notifications/webhooks \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Gotify",
    "enabled": true,
    "event_type": "rss_item_new",
    "url_template": "http://gotify.local/message?token=abc123",
    "method": "POST",
    "feed_scope": "all",
    "feed_ids": [],
    "query_params": [],
    "headers": [{"key":"Content-Type","value":"application/json"}],
    "body_mode": "raw",
    "body_fields": [],
    "body_template": "{\"title\":\"ThreatLens Alert\",\"message\":\"{{ item.title }}\",\"priority\":5}",
    "timeout_seconds": 10
  }'
```

Queue a Daily Brief after AI is configured:

```bash
curl -X POST http://localhost:8000/ai/daily-brief/queue \
  -H "Authorization: Bearer $TOKEN"
```

Preview a custom tagging rule before creating it:

```bash
curl -X POST http://localhost:8000/tagging/rules/preview \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Fortinet Vendor",
    "tag_name": "vendor:fortinet",
    "enabled": true,
    "match_type": "contains",
    "pattern": "fortinet",
    "case_sensitive": false,
    "applies_to": ["title", "article_text"],
    "required_categories": ["vulnerability"],
    "feed_scope": "all",
    "feed_ids": [],
    "min_classification_confidence": 0.6,
    "limit": 5
  }'
```

## Roles

### `admin`

- Full access
- Can manage users, tokens, audit logs, feeds

### `analyst`

- Works with feeds, tags, and triage
- Cannot manage users or global audit logs

### `viewer`

- Read-only access

## API Tokens

Create a token:

```bash
curl -X POST http://localhost:8000/tokens \
  -H "Authorization: Bearer <jwt>" \
  -H "Content-Type: application/json" \
  -d '{"name":"ci-agent","expires_in_days":30,"scopes":["read:feeds"]}'
```

Use it:

```bash
curl http://localhost:8000/feeds \
  -H "Authorization: Bearer <token>"
```

Revoke it:

```bash
curl -X DELETE http://localhost:8000/tokens/<token_id> \
  -H "Authorization: Bearer <jwt>"
```

Notes:

- Default scopes are read-only if none are provided
- `write:*` implies `read:*`
- Wildcards are supported (`read:*`, `admin:*`)
- Legacy unscoped tokens are disabled by default

## User Management (admin only)

Create a user:

```bash
curl -X POST http://localhost:8000/users \
  -H "Authorization: Bearer <admin_jwt>" \
  -H "Content-Type: application/json" \
  -d '{"email":"analyst@example.com","password":"StrongPass123!","role":"analyst"}'
```

Update a user:

```bash
curl -X PATCH http://localhost:8000/users/<user_id> \
  -H "Authorization: Bearer <admin_jwt>" \
  -H "Content-Type: application/json" \
  -d '{"role":"viewer","is_active":false}'
```

## Audit Logs

Fetch logs:

```bash
curl "http://localhost:8000/audit-logs?page=1&page_size=50" \
  -H "Authorization: Bearer <admin_jwt>"
```

Filter by action:

```bash
curl "http://localhost:8000/audit-logs?action=feeds.create" \
  -H "Authorization: Bearer <admin_jwt>"
```

## Local Development

### Backend

```bash
cd backend
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload
```

Run workers:

```bash
celery -A app.tasks.celery_app.celery_app worker --loglevel=INFO
celery -A app.tasks.celery_app.celery_app beat --loglevel=INFO
```

### Frontend

```bash
cd web
npm install
npm run dev
```

## Testing

Run tests inside Docker:

```bash
docker compose build api
docker compose run --rm -e HOME=/tmp api sh -lc \
  "pip install --no-cache-dir -r requirements-dev.txt && python -m pytest"
```

## Backup & Restore

Backup:

```bash
docker compose exec db pg_dump -U postgres threatlens > backup.sql
```

Restore:

```bash
cat backup.sql | docker compose exec -T db psql -U postgres threatlens
```
