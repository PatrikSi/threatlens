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
- JWT-backed browser session cookies and personal API tokens
- Audit logging for security-relevant actions
- Multi-window dashboard with RSS, alerts, and notes panes
- Optional AI workspace for admin-managed endpoint configuration, task operations, usage stats, and prompt/profile tuning
- AI item enrichment with per-article summary + relevance scoring, plus a dashboard Daily Brief widget
- Per-user triage state (read, starred, notes, tags) and saved dashboard views
- Alert interests with live preview before save
- Personal webhook notifications with template variables, admin-governed destination controls, multi-event delivery history, analytics, retry, and test-send support
- Admin tagging controls with custom rules, preview, and background reapply
- Feed scheduling, metadata detection, import/export, and manual refresh controls
- Article fetching, readable content extraction, classification, and IOC extraction
- Stats dashboards for feed health, activity, domains, and signal distribution

## Setup

Copy the environment template:

```bash
cp .env.example .env
```

The provided `docker-compose.yml` expects a real `.env` file. If you deploy with another orchestrator that injects environment variables directly, treat `.env.example` as the canonical reference and adapt the manifests instead of using the compose file unchanged.

You'll need to configure at least:

- `DATABASE_URL`
- `REDIS_URL`
- `JWT_SECRET`
- `APP_DATA_ENCRYPTION_KEY`
- `ADMIN_EMAIL`
- `ADMIN_PASSWORD`

Use a distinct `APP_DATA_ENCRYPTION_KEY` for webhook/request encryption at rest instead of reusing `JWT_SECRET`. If you are upgrading an existing install and want older encrypted rows to remain readable during key rotation, keep the old encryption input in `APP_DATA_ENCRYPTION_PREVIOUS_KEYS` until the stored secrets have been rewritten.

Outbound webhook governance:

- Admins can always manage their own notification webhooks.
- Analysts can only create, update, test, or retry webhook deliveries after an admin configures `NOTIFICATION_WEBHOOK_ALLOWED_HOSTS`.
- `NOTIFICATION_WEBHOOK_ALLOWED_HOSTS` accepts a comma-separated list of exact hosts or `*.suffix` patterns, for example `hooks.slack.com,*.logic.azure.com`.

Secure defaults in the shipped template:

- `APP_ENV=production`
- `AUTH_COOKIE_SECURE=true`
- `SEED_ADMIN_ON_STARTUP=false`
- `EXPOSE_API_DOCS_IN_PRODUCTION=false`

For an HTTP-only local evaluation, set `APP_ENV=development` and `AUTH_COOKIE_SECURE=false` before first startup.

Admin startup behavior:

- `SEED_ADMIN_ON_STARTUP=true` seeds `ADMIN_EMAIL`/`ADMIN_PASSWORD` on first startup.
- `SEED_ADMIN_RESET_PASSWORD_ON_STARTUP=true` forces that account password to be reset at startup (useful during controlled credential rotation).
- After initial setup, set `SEED_ADMIN_RESET_PASSWORD_ON_STARTUP=false` unless you intentionally want startup-driven password resets.

There are a number of additional flags for auth hardening, rate limiting, and feed handling - check `.env.example` for full details.

Release-contract artifacts shipped in the repo:

- Generated API reference: `docs/reference/api.md`
- OpenAPI schema snapshot: `docs/reference/openapi.json`
- Backend runtime lockfile: `backend/requirements-lock.txt`
- Runtime dependency inventories: `docs/reference/backend-runtime-dependencies.txt`, `docs/reference/frontend-runtime-dependencies.txt`
- Release/support workflow: `docs/reference/release-process.md`
- Third-party notices: `THIRD_PARTY_NOTICES.md`
- Bundled license texts: `docs/licenses/`
- Governance/community docs: `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, `CHANGELOG.md`

Repository-hosted support and reporting paths:

- Questions and bug reports: `https://github.com/PatrikSi/threatlens/issues`
- Pull requests: `https://github.com/PatrikSi/threatlens/pulls`
- Security coordination entry point: `https://github.com/PatrikSi/threatlens/security`
- Maintainer profile: `https://github.com/PatrikSi`

## Running with Docker

Start everything:

```bash
docker compose up --build -d
```

Startup flow for `docker-compose.yml`:

- `api` runs migrations on startup by default and can also seed the admin account when `SEED_ADMIN_ON_STARTUP=true`.
- `worker` and `beat` wait for healthy `api`, plus healthy DB/Redis, before starting steady-state work.
- `beat` runs as its own container so periodic jobs do not multiply with worker replicas.
- `worker` and `beat` keep schema/admin startup mutations disabled.
- Only the `web` service is published by default. The API stays internal to the compose network and the shipped browser build targets the versioned proxy base at `/api/v1`.
- `WEB_VITE_API_BASE_URL` defaults to `/api/v1` in the provided `.env.example`. For non-proxied deployments, set it to the full versioned API origin such as `https://api.example.com/v1`.
- The machine-readable OpenAPI schema remains published separately at `/api/openapi.json`.
- Legacy unversioned backend routes remain available for compatibility, but they are not the documented or shipped runtime contract.
- Both shipped container images place release-compliance metadata under `/usr/share/doc/threatlens/`, including notices, bundled license texts, and runtime dependency inventories.

The production-oriented `.env.example` assumes the browser reaches ThreatLens over HTTPS, typically through a reverse proxy in front of the `web` container. For a localhost-only HTTP trial, switch the auth cookie settings back to development-safe values before first boot.

For horizontally scaled production, run migrations/admin seeding from one controlled deploy or init step before scaling API replicas. The default `docker-compose.yml` is optimized for a single API instance handling startup mutations safely.

Check containers:

```bash
docker compose ps
```

Endpoints:

- UI: `http://localhost:3000`
- OpenAPI schema: `http://localhost:3000/api/openapi.json`
- API health: `http://localhost:3000/api/v1/health`
- Readiness: `http://localhost:3000/api/v1/health/ready`
- Worker: `http://localhost:3000/api/v1/health/worker`
- Beat: `http://localhost:3000/api/v1/health/beat`

Published path summary:

- Browser/API base: `/api/v1`
- OpenAPI schema: `/api/openapi.json`
- Internal backend versioned base: `/v1`

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

2. If admin is missing at startup, ensure these values are explicitly set on the API service startup:
   - `SEED_ADMIN_ON_STARTUP=true`
   - `ADMIN_EMAIL=<value>`
   - `ADMIN_PASSWORD=<value>`
   - `RUN_MIGRATIONS_ON_STARTUP=true`
   - Keep those flags disabled on worker and beat replicas.

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
curl -X POST http://localhost:3000/api/v1/feeds/<feed_id>/refresh \
  -H "Authorization: Bearer <api_token>"
```

## Local Validation

- Backend test suite:

```bash
./backend/.venv/bin/pytest backend/tests -q
```

- Frontend production build:

```bash
# Uses `/api/v1` as the default production API base.
# For non-proxied deployments, also set `THREATLENS_CSP_CONNECT_SRC` on the web container
# to include the external API origin, e.g. `THREATLENS_CSP_CONNECT_SRC="'self' https://api.example.com"`.
docker build -q -f web/Dockerfile web
```

- Runtime smoke:

```bash
docker compose up -d --build api worker beat web
curl http://localhost:3000/api/v1/health/ready
```

## Auth Model

- `POST /api/v1/auth/login` returns JSON containing `token_type=session_cookie` and `csrf_token`, and also sets the browser session cookies.
- The shipped React app uses the cookie session (`AUTH_COOKIE_NAME`) and does not store the JWT in local storage.
- Browser login no longer returns a replayable session JWT in the response body.
- CLI and automation callers should mint personal API tokens from `/api/v1/tokens` and use those as `Authorization: Bearer <token>`.
- Cookie-session callers minting durable API tokens from `/api/v1/tokens` must also provide `current_password` as a step-up check.
- Cookie-authenticated `POST`, `PUT`, `PATCH`, and `DELETE` requests must echo the CSRF cookie value in `AUTH_CSRF_HEADER_NAME`.
- If a request sends both an `Authorization` header and the session cookie, the header wins. An invalid header does not fall back to the cookie.

## Trust Boundaries

- ThreatLens is a self-hosted, single-deployment application for one team or organization. It does not implement tenant isolation between separate customers.
- The platform makes outbound requests to configured feed/article URLs, an optional AI endpoint, and user-configured webhook destinations. Private-network access is disabled by default and controlled separately for fetch, AI, and webhook traffic. Analyst-managed webhook destinations are denied by default unless `NOTIFICATION_WEBHOOK_ALLOWED_HOSTS` is configured.
- Browser dashboard layouts, read state, and scratch notes are stored in local browser storage per user. Session credentials remain in cookies rather than browser storage.
- Webhook templates and delivery snapshots are encrypted at rest with `APP_DATA_ENCRYPTION_KEY`, but authorized users can still view decrypted previews through the application.
- AI summaries, daily briefs, and usage history are stored in the application database. Provider-exchange inspection stores sanitized request/response metadata, not full raw provider transcripts.

## API Examples

Log in and mint a scoped API token for CLI use:

```bash
COOKIE_JAR=$(mktemp)
CSRF=$(curl -sS -c "$COOKIE_JAR" http://localhost:3000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"<admin-password>"}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["csrf_token"])')
TOKEN=$(curl -sS -b "$COOKIE_JAR" \
  -H "X-CSRF-Token: $CSRF" \
  -H "Content-Type: application/json" \
  -d '{"name":"admin-demo-token","expires_in_days":30,"scopes":["*:*"],"current_password":"<admin-password>"}' \
  http://localhost:3000/api/v1/tokens \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')
rm -f "$COOKIE_JAR"
```

The example token above is intentionally broad so the later admin/operator examples work as written. For real automation, mint narrower scopes.

Preview an alert before saving it:

```bash
curl -X POST http://localhost:3000/api/v1/alerts/preview \
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
curl -X POST http://localhost:3000/api/v1/notifications/webhooks \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Webhook Endpoint",
    "enabled": true,
    "event_type": "rss_item_new",
    "url_template": "https://hooks.example.com/threatlens?token=replace-me",
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

If the caller is an `analyst`, the webhook host must match `NOTIFICATION_WEBHOOK_ALLOWED_HOSTS`. Admin-managed webhooks are not constrained by that allowlist.

Queue a Daily Brief after AI is configured:

```bash
curl -X POST http://localhost:3000/api/v1/ai/daily-brief/queue \
  -H "Authorization: Bearer $TOKEN"
```

Preview a custom tagging rule before creating it:

```bash
curl -X POST http://localhost:3000/api/v1/tagging/rules/preview \
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
curl -X POST http://localhost:3000/api/v1/tokens \
  -H "Authorization: Bearer <api_token>" \
  -H "Content-Type: application/json" \
  -d '{"name":"ci-agent","expires_in_days":30,"scopes":["read:feeds"]}'
```

Use it:

```bash
curl http://localhost:3000/api/v1/feeds \
  -H "Authorization: Bearer <token>"
```

Revoke it:

```bash
curl -X DELETE http://localhost:3000/api/v1/tokens/<token_id> \
  -H "Authorization: Bearer <api_token>"
```

Notes:

- Omit `scopes` to get the default read-only token scopes; an explicit empty list is rejected
- `write:*` implies `read:*`
- Wildcards are supported (`read:*`, `admin:*`)
- Tokens created while already authenticated with an API token can only delegate a subset of the parent token's scopes
- Legacy unscoped tokens are disabled by default

## Redistribution Notes

- `THIRD_PARTY_NOTICES.md` summarizes the bundled assets, selected direct runtime dependencies, redistribution notes, and regeneration commands for the committed runtime inventories.
- `docs/reference/backend-runtime-dependencies.txt` and `docs/reference/frontend-runtime-dependencies.txt` are the full resolved runtime inventories committed with the source tree.
- Built backend images also include `/usr/share/doc/threatlens/backend-runtime-dependencies.txt` and `/usr/share/doc/threatlens/backend-requirements.txt`.
- `docs/licenses/OFL-1.1.txt` covers the bundled Source Sans 3 and Space Grotesk font files shipped in `web/public/fonts/`.
- `LICENSE` provides the Apache-2.0 license text used by the project and third-party Apache-2.0 components.
- `docs/licenses/MIT.txt`, `docs/licenses/BSD-2-Clause.txt`, `docs/licenses/BSD-3-Clause.txt`, `docs/licenses/ISC.txt`, `docs/licenses/MPL-2.0.txt`, and `docs/licenses/Unlicense.txt` are bundled for common third-party runtime licenses in the shipped stack.
- `docs/licenses/LGPL-3.0.txt` and `docs/licenses/GPL-3.0.txt` are shipped for the `psycopg[binary]` backend dependency. If your redistribution program prefers locally linked PostgreSQL client libraries, rebuild the backend image with a non-binary psycopg install before distributing.

## User Management (admin only)

Create a user:

```bash
curl -X POST http://localhost:3000/api/v1/users \
  -H "Authorization: Bearer <admin_api_token>" \
  -H "Content-Type: application/json" \
  -d '{"email":"analyst@example.com","password":"StrongPass123!","role":"analyst"}'
```

Update a user:

```bash
curl -X PATCH http://localhost:3000/api/v1/users/<user_id> \
  -H "Authorization: Bearer <admin_api_token>" \
  -H "Content-Type: application/json" \
  -d '{"role":"viewer","is_active":false}'
```

## Audit Logs

Fetch logs:

```bash
curl "http://localhost:3000/api/v1/audit-logs?page=1&page_size=50" \
  -H "Authorization: Bearer <admin_api_token>"
```

Filter by action:

```bash
curl "http://localhost:3000/api/v1/audit-logs?action=feeds.create" \
  -H "Authorization: Bearer <admin_api_token>"
```

## Local Development

### Backend

```bash
cd backend
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload
```

Optional one-time startup helper:

```bash
RUN_MIGRATIONS_ON_STARTUP=true SEED_ADMIN_ON_STARTUP=true ./scripts/start-api.sh
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
