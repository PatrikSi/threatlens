# ThreatLens

ThreatLens is a self-hosted cyber threat intelligence aggregator for security operations teams.

## Architecture

- `web`: React + TypeScript UI
- `api`: FastAPI app
- `worker`: Celery workers for feed/article jobs
- `beat`: Celery beat scheduler
- `db`: PostgreSQL
- `redis`: queue and coordination backend

## Enterprise Features

- Multi-user accounts
- RBAC roles: `admin`, `analyst`, `viewer`
- User lifecycle management (`/users` admin APIs)
- JWT auth plus personal API tokens (`/tokens`)
- Audit logs for operational/security actions (`/audit-logs`)
- Read/star/note/tag triage state per user
- Feed scheduling + refresh controls
- Article dereference and readable text extraction

## Environment

Create `.env` from template:

```bash
cp .env.example .env
```

Key vars:

- `DATABASE_URL`
- `REDIS_URL`
- `JWT_SECRET`
- `ADMIN_EMAIL`
- `ADMIN_PASSWORD`
- `ALLOW_SELF_REGISTRATION` (default `false`)
- `DEFAULT_API_TOKEN_EXPIRY_DAYS` (default `90`)
- `ALLOW_LEGACY_UNSCOPED_TOKENS` (default `true`)
- `ALLOW_PRIVATE_NETWORK_FETCH` (default `false`)
- `FEED_MAX_BYTES` (default `2000000`)
- `OUTBOUND_MAX_REDIRECTS` (default `5`)
- `AUTH_LOGIN_MAX_ATTEMPTS` (default `8`)
- `AUTH_LOGIN_WINDOW_SECONDS` (default `300`)
- `AUTH_LOGIN_LOCKOUT_SECONDS` (default `900`)
- `API_TOKEN_LAST_USED_UPDATE_INTERVAL_SECONDS` (default `300`)

## Run (Docker Compose)

Start all services:

```bash
docker compose up --build -d
```

Check status:

```bash
docker compose ps
```

Open:

- Web UI: `http://localhost:3000`
- Health: `http://localhost:8000/health`
- Liveness: `http://localhost:8000/health/live`
- Readiness: `http://localhost:8000/health/ready`

Stop:

```bash
docker compose down
```

Stop + remove volumes:

```bash
docker compose down -v
```

## Backend Operations

### Apply migrations

```bash
docker compose exec api alembic upgrade head
```

### Create/refresh admin user

```bash
docker compose exec api python -m app.scripts.seed_admin
```

### Inspect API logs

```bash
docker compose logs -f api
```

### Worker/beat logs

```bash
docker compose logs -f worker beat
```

### Manual feed refresh (API)

```bash
curl -X POST http://localhost:8000/feeds/<feed_id>/refresh \
  -H "Authorization: Bearer <jwt>"
```

## RBAC Model

- `admin`
  - Full access
  - Manage users, audit logs, tokens (all users), feeds
- `analyst`
  - Manage feeds, tags, triage state, personal tokens
  - Cannot manage users or global audit logs
- `viewer`
  - Read-only access to feed/item data and personal views/tokens
  - Cannot mutate feeds/tags/triage state

## API Tokens

Create token (JWT-authenticated):

```bash
curl -X POST http://localhost:8000/tokens \
  -H "Authorization: Bearer <jwt>" \
  -H "Content-Type: application/json" \
  -d '{"name":"ci-agent","expires_in_days":30,"scopes":["read:feeds"]}'
```

Notes:

- If `scopes` is omitted, token defaults to `read:feeds`, `read:items`, `read:stats`.
- Legacy tokens with empty scopes are allowed only when `ALLOW_LEGACY_UNSCOPED_TOKENS=true`.
- `write:<resource>` implies `read:<resource>`.
- Wildcards supported: `read:*`, `write:*`, `admin:*`.
- Supported resources: `feeds`, `items`, `tags`, `views`, `tokens`, `users`, `audit`, `stats`.

Use token:

```bash
curl http://localhost:8000/feeds \
  -H "Authorization: Bearer <plain_api_token>"
```

Revoke token:

```bash
curl -X DELETE http://localhost:8000/tokens/<token_id> \
  -H "Authorization: Bearer <jwt>"
```

## User Admin API (admin role)

Create user:

```bash
curl -X POST http://localhost:8000/users \
  -H "Authorization: Bearer <admin_jwt>" \
  -H "Content-Type: application/json" \
  -d '{"email":"analyst@example.com","password":"StrongPass123!","role":"analyst","is_active":true}'
```

Update user:

```bash
curl -X PATCH http://localhost:8000/users/<user_id> \
  -H "Authorization: Bearer <admin_jwt>" \
  -H "Content-Type: application/json" \
  -d '{"role":"viewer","is_active":false}'
```

## Audit Logs (admin role)

Fetch latest:

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

### Backend (without Docker)

```bash
cd backend
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

## Testing

Comprehensive test suite includes:

- Unit tests for URL normalization, dedupe, extraction
- API integration tests for auth, RBAC, tokens, audit logs

Run tests in container (recommended):

```bash
docker compose build api
docker compose run --rm -e HOME=/tmp api sh -lc "python -m pip install --user --no-cache-dir -r requirements-dev.txt && PATH=/tmp/.local/bin:$PATH pytest"
```

## Backup/Restore

Backup PostgreSQL:

```bash
docker compose exec db pg_dump -U postgres threatlens > threatlens_backup.sql
```

Restore PostgreSQL:

```bash
cat threatlens_backup.sql | docker compose exec -T db psql -U postgres threatlens
```

## Notes

- OpenAI enrichment remains intentionally unimplemented for now (future async pipeline stage).
- API token plaintext is shown only once at creation time.
- Audit logs are append-only records for operational traceability.
- Structured UI documentation is available under `docs/`.
