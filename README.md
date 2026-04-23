# ThreatLens

ThreatLens is a self-hosted threat intelligence aggregator built for security teams. It pulls in feeds, processes articles, and gives analysts a clean interface to triage and track what matters.

## Stack Overview

The project is split into a few core services:

- `web` - React + TypeScript frontend
- `api` - FastAPI backend
- `worker` - Celery worker for ingestion, processing, notifications, and maintenance queues
- `ai-worker` - dedicated Celery worker for AI enrichment and daily brief jobs
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
- `REQUIRE_EXPLICIT_DATA_ENCRYPTION_KEY=true` for durable deployments
- `ADMIN_EMAIL`
- `ADMIN_PASSWORD`

Use a distinct `APP_DATA_ENCRYPTION_KEY` for webhook/request encryption at rest instead of reusing `JWT_SECRET`. If you are upgrading an existing install and want older encrypted rows to remain readable during key rotation, keep the old encryption input in `APP_DATA_ENCRYPTION_PREVIOUS_KEYS` until the stored secrets have been rewritten. In non-production, missing placeholder secrets still fall back to deterministic development-only values derived from the local runtime settings for throwaway workflows, but the shipped compose deployment now sets `REQUIRE_EXPLICIT_DATA_ENCRYPTION_KEY=true` so persistent stacks fail fast instead of silently creating unrecoverable data.

Outbound webhook governance:

- Admins can always manage their own notification webhooks.
- Analysts can only create, update, test, or retry webhook deliveries after an admin configures `NOTIFICATION_WEBHOOK_ALLOWED_HOSTS`.
- `NOTIFICATION_WEBHOOK_ALLOWED_HOSTS` accepts a comma-separated list of exact hosts, exact `host:port` pairs, wildcard subdomains, or full `http(s)` URL prefixes, for example `hooks.slack.com,https://hooks.example.com/services/tenant-a,*.logic.azure.com`.
- Plain host entries approve the default `https` origin for that host. Use an explicit `host:port` or full URL prefix entry to allow a non-default port or a tenant-scoped path, and `*.suffix` only matches subdomains, not the apex `suffix`.

Secure defaults in the shipped template:

- `APP_ENV=production`
- `AUTH_COOKIE_SECURE=true`
- `REQUIRE_EXPLICIT_DATA_ENCRYPTION_KEY=true`
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
- Backend Python application lockfile: `backend/requirements-lock.txt`
- Runtime dependency inventories: `docs/reference/backend-runtime-dependencies.txt`, `docs/reference/frontend-runtime-dependencies.txt`
- Runtime package metadata inventories: `docs/reference/backend-runtime-package-metadata.json`, `docs/reference/frontend-runtime-package-metadata.json`
- Backend runtime package legal artifacts: `docs/reference/backend-runtime-package-legal/`
- Frontend runtime package legal artifacts: `docs/reference/frontend-runtime-package-legal/`
- Image OS package notice artifacts: `docs/reference/backend-os-packages.txt`, `docs/reference/backend-os-package-legal/`, `docs/reference/frontend-os-packages.txt`, `docs/reference/frontend-os-package-metadata.tsv`, `docs/reference/frontend-os-package-legal/`
- Release/support workflow: `docs/reference/release-process.md`
- Third-party notices: `THIRD_PARTY_NOTICES.md`
- Bundled common license texts: `docs/licenses/`
- Governance/community docs: `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, `CHANGELOG.md`

Project status and support posture:

- The configured GitHub origin for this checkout is `https://github.com/PatrikSi/threatlens`, and the repository is currently private.
- Tagged releases, when published, are the preferred upgrade anchors for operators.
- Until the first public tag exists, pin the exact commit SHA and container image digest you deploy. The default branch is the active development line, not a separate LTS channel.
- GitHub issues and pull requests are currently collaborator workflows inside the private repository, not a public support or disclosure channel.
- Maintainer responses remain best-effort rather than a contractual support SLA.

Community and reporting paths:

- Repository URL: `https://github.com/PatrikSi/threatlens`
- Collaborator issue tracker: `https://github.com/PatrikSi/threatlens/issues`
- Collaborator pull requests: `https://github.com/PatrikSi/threatlens/pulls`
- Security reporting policy: `SECURITY.md` for the current collaborator-visible first-contact process; ThreatLens does not currently publish a dedicated private inbox or a verified GitHub private advisory submission path
- Conduct and moderation policy: `CODE_OF_CONDUCT.md`; ThreatLens does not currently publish a dedicated private maintainer conduct inbox in-repo
- If you do not already have repository access, this tree does not yet publish a public issue tracker, public discussion forum, or repo-owned confidential reporting path

## Running with Docker

Start everything:

```bash
export BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
export VCS_REF="$(git rev-parse HEAD)"
docker compose up --build -d
```

Startup flow for `docker-compose.yml`:

- `api` runs migrations on startup by default and can also seed the admin account when `SEED_ADMIN_ON_STARTUP=true`.
- `worker`, `ai-worker`, and `beat` wait for healthy `api`, plus healthy DB/Redis, before starting steady-state work.
- `beat` runs as its own container so periodic jobs do not multiply with worker replicas.
- `worker` handles the non-AI queues (`ingest`, `processing`, `notifications`, `maintenance`) so manual feed refresh and scheduled polling stay responsive even when AI work is busy.
- `ai-worker` isolates long-running AI enrichment and daily brief jobs onto the `ai` queue.
- `worker`, `ai-worker`, and `beat` keep schema/admin startup mutations disabled.
- Only the `web` service is published by default. The API stays internal to the compose network and the shipped browser build targets the versioned proxy base at `/api/v1`.
- `WEB_VITE_API_BASE_URL` defaults to `/api/v1` in the provided `.env.example`. For non-proxied deployments, set it to the full versioned API origin such as `https://api.example.com/v1`.
- The shipped compose stack treats its named Postgres and Redis volumes as durable and therefore requires an explicit `APP_DATA_ENCRYPTION_KEY` by setting `REQUIRE_EXPLICIT_DATA_ENCRYPTION_KEY=true` on `api`, `worker`, `ai-worker`, and `beat`.
- `docker-compose.yml` forwards exported `BUILD_DATE` and `VCS_REF` values into every built ThreatLens image so the standard `docker compose build` and `docker compose up --build` flow stamps OCI labels with the checked-out revision and build time. If you do not export them first, those labels fall back to `unknown`.
- The machine-readable OpenAPI schema remains published separately at `/api/openapi.json`.
- The bundled web proxy publishes only `/api/v1/*` plus `/api/openapi.json`; other `/api/*` paths are intentionally outside the shipped browser/runtime contract.
- Both shipped container images place release-compliance metadata under `/usr/share/doc/threatlens/`. The backend image ships a discoverable `README.md`, backend notices, Python dependency inventories, `backend-runtime-package-legal/`, `backend-os-packages.txt`, and `backend-os-package-legal/`. The web image ships its own `README.md`, frontend package metadata, `frontend-runtime-package-legal/`, `frontend-os-packages.txt`, `frontend-os-package-metadata.tsv`, and `frontend-os-package-legal/`.
- The bundled compose stack reserves `172.31.240.0/24` for the `web` frontend network and trusts that exact subnet by default so browser auth throttling can recover the real client IP through the shipped proxy. If you deploy behind different proxies, set `TRUSTED_PROXY_CIDRS` to the exact hop CIDRs you control instead of a broad Docker bridge range.

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
- Encrypted data inventory: `http://localhost:3000/api/v1/health/encrypted-data` (admin only)

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

4. If deployed behind one or more reverse proxies, set `TRUSTED_PROXY_CIDRS` to the exact proxy hops you control so IP-based auth throttling can walk the preserved chain back to the real client IP. The bundled compose file already trusts its reserved `web` frontend subnet `172.31.240.0/24`.

5. If you change `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, or `REDIS_PASSWORD` after the stack has already initialized its Docker volumes, update `DATABASE_URL` and `REDIS_URL` to match and then either migrate the stored service state or recreate those volumes. Older local `.env` files that omit the explicit Postgres/Redis credential variables continue to fall back to the backend-compatible `postgres` / `redis://redis:6379/0` defaults, but the production-oriented `.env.example` values should be treated as the canonical long-term settings for new installs.

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
BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)" VCS_REF="$(git rev-parse HEAD)" \
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

If the caller is an `analyst`, the webhook target must match `NOTIFICATION_WEBHOOK_ALLOWED_HOSTS`: plain host entries map to the default `https` origin, exact `host:port` or full URL prefix entries can approve non-default ports or tenant-scoped paths, and `*.suffix` does not include the apex `suffix`. Admin-managed webhooks are not constrained by that allowlist.

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
- `docs/reference/backend-runtime-package-metadata.json` and `docs/reference/frontend-runtime-package-metadata.json` capture package-specific metadata used for redistribution review, including copied package-legal artifact paths and digests where those files are published by upstream packages.
- `docs/reference/backend-runtime-package-legal/` preserves wheel-published legal files harvested from installed backend runtime dependencies when those files are present in the redistributed Python distributions.
- `docs/reference/frontend-runtime-package-legal/` preserves the package-published legal files harvested from installed frontend runtime dependencies.
- `docs/reference/backend-os-packages.txt` plus `docs/reference/backend-os-package-legal/` document the Debian packages and copied package copyright files redistributed by the built backend image.
- `docs/reference/frontend-os-packages.txt`, `docs/reference/frontend-os-package-metadata.tsv`, and `docs/reference/frontend-os-package-legal/` document the Alpine packages redistributed by the built web image.
- Built backend images also include `/usr/share/doc/threatlens/backend-runtime-dependencies.txt`, `/usr/share/doc/threatlens/backend-runtime-package-metadata.json`, `/usr/share/doc/threatlens/backend-runtime-package-legal/`, `/usr/share/doc/threatlens/backend-os-packages.txt`, `/usr/share/doc/threatlens/backend-os-package-legal/`, `/usr/share/doc/threatlens/backend-requirements.txt`, and `/usr/share/doc/threatlens/backend-requirements-lock.txt`.
- Built web images also include `/usr/share/doc/threatlens/frontend-runtime-dependencies.txt`, `/usr/share/doc/threatlens/frontend-runtime-package-metadata.json`, `/usr/share/doc/threatlens/frontend-runtime-package-legal/`, `/usr/share/doc/threatlens/frontend-package-lock.json`, `/usr/share/doc/threatlens/frontend-os-packages.txt`, `/usr/share/doc/threatlens/frontend-os-package-metadata.tsv`, and `/usr/share/doc/threatlens/frontend-os-package-legal/`.
- `backend/requirements-lock.txt` pins the backend Python application dependency layer installed by `backend/Dockerfile`; it is not a complete inventory of every Debian package redistributed in the final image.
- `docs/licenses/OFL-1.1.txt` covers the bundled Source Sans 3 and Space Grotesk font files shipped in `web/public/fonts/`.
- `LICENSE` provides the Apache-2.0 license text used by the project and third-party Apache-2.0 components.
- `docs/licenses/Apache-2.0.txt`, `docs/licenses/MIT.txt`, `docs/licenses/BSD-2-Clause.txt`, `docs/licenses/BSD-3-Clause.txt`, `docs/licenses/ISC.txt`, `docs/licenses/MPL-2.0.txt`, `docs/licenses/MPL-1.1.txt`, `docs/licenses/Unlicense.txt`, `docs/licenses/GPL-2.0.txt`, `docs/licenses/GPL-3.0.txt`, `docs/licenses/LGPL-2.1.txt`, and `docs/licenses/LGPL-3.0.txt` are bundled as shared license-family references for the shipped application stack and committed assets.
- Package-specific runtime and OS-layer legal bundles remain the authoritative per-package redistribution records. If your redistribution program prefers locally linked PostgreSQL client libraries, rebuild the backend image with a non-binary psycopg install before distributing.

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

Use the checked-in dependency locks for local installs. `backend/requirements-dev.txt` is the backend dev/test entry point and includes the pinned application set from `backend/requirements-lock.txt`. For the frontend, prefer `npm ci` so installs match `web/package-lock.json`.

### Backend

```bash
cd backend
python3 -m venv .venv
./.venv/bin/pip install -r requirements-dev.txt
./.venv/bin/alembic upgrade head
./.venv/bin/uvicorn app.main:app --reload
```

Optional one-time startup helper from the repository root:

```bash
RUN_MIGRATIONS_ON_STARTUP=true SEED_ADMIN_ON_STARTUP=true ./backend/scripts/start-api.sh
```

Run workers:

```bash
./.venv/bin/celery -A app.tasks.celery_app.celery_app worker --loglevel=INFO
./.venv/bin/celery -A app.tasks.celery_app.celery_app beat --loglevel=INFO
```

### Frontend

```bash
cd web
npm ci
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

These commands assume the production-oriented `.env.example` values: `POSTGRES_USER=threatlens` and `POSTGRES_DB=threatlens`. If your local `.env` overrides them, substitute your effective values.

Backup:

```bash
docker compose exec -T db pg_dump -U threatlens threatlens > backup.sql
```

Restore:

```bash
docker compose exec -T db psql -U threatlens threatlens < backup.sql
```
