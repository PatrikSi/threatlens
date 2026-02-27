# Configuration and Deployment

## Runtime Topology

`docker-compose.yml` defines these services:

- `db`: PostgreSQL 16 (`5432`)
- `redis`: Redis 7 (`6379`)
- `api`: FastAPI (`8000`)
- `worker`: Celery worker
- `beat`: Celery beat scheduler
- `web`: Nginx serving Vite build (`3000`)

## Backend Settings (`backend/app/core/config.py`)

`Settings` is loaded from `.env` (via `pydantic-settings`) with these defaults:

| Variable | Default | Purpose |
|---|---:|---|
| `APP_ENV` (`app_env`) | `development` | Environment mode, drives production validation rules. |
| `DATABASE_URL` (`database_url`) | `postgresql+psycopg://postgres:postgres@db:5432/threatlens` | SQLAlchemy database URL. |
| `REDIS_URL` (`redis_url`) | `redis://redis:6379/0` | Celery broker/result backend and worker coordination. |
| `JWT_SECRET` (`jwt_secret`) | `change-me` | JWT signing key. |
| `JWT_ALGORITHM` (`jwt_algorithm`) | `HS256` | JWT signature algorithm. |
| `JWT_EXPIRES_MINUTES` (`jwt_expires_minutes`) | `1440` | Access token TTL in minutes. |
| `ALLOW_LEGACY_UNSCOPED_TOKENS` (`allow_legacy_unscoped_tokens`) | `true` | Whether API tokens with empty scope lists are accepted. |
| `ALLOW_SELF_REGISTRATION` (`allow_self_registration`) | `false` | Enables/disables `/auth/register`. |
| `DEFAULT_API_TOKEN_EXPIRY_DAYS` (`default_api_token_expiry_days`) | `90` | Default token lifetime if not supplied. |
| `ADMIN_EMAIL` (`admin_email`) | `admin@example.com` | Seed admin identity. |
| `ADMIN_PASSWORD` (`admin_password`) | `admin123` | Seed admin password. |
| `FETCH_USER_AGENT` (`fetch_user_agent`) | `ThreatLensBot/1.0 (+https://localhost)` | User-Agent for feed/article HTTP requests. |
| `FEED_CONNECT_TIMEOUT_SECONDS` (`feed_connect_timeout_seconds`) | `5` | Feed HTTP connect timeout. |
| `FEED_READ_TIMEOUT_SECONDS` (`feed_read_timeout_seconds`) | `15` | Feed HTTP read timeout. |
| `FEED_MAX_BYTES` (`feed_max_bytes`) | `2000000` | Max feed response size before rejection. |
| `ARTICLE_CONNECT_TIMEOUT_SECONDS` (`article_connect_timeout_seconds`) | `5` | Article HTTP connect timeout. |
| `ARTICLE_READ_TIMEOUT_SECONDS` (`article_read_timeout_seconds`) | `20` | Article HTTP read timeout. |
| `ARTICLE_MAX_BYTES` (`article_max_bytes`) | `4000000` | Max article response size before rejection. |
| `OUTBOUND_MAX_REDIRECTS` (`outbound_max_redirects`) | `5` | Redirect hop cap for outbound fetches. |
| `PER_DOMAIN_CONCURRENCY` (`per_domain_concurrency`) | `2` | Redis-coordinated per-domain concurrent article fetch cap. |
| `ALLOW_PRIVATE_NETWORK_FETCH` (`allow_private_network_fetch`) | `false` | Allows/disallows private/loopback fetch targets. |
| `AUTH_LOGIN_MAX_ATTEMPTS` (`auth_login_max_attempts`) | `8` | Failed login attempts allowed in window before temporary lockout. |
| `AUTH_LOGIN_WINDOW_SECONDS` (`auth_login_window_seconds`) | `300` | Sliding window for failed login attempt counting. |
| `AUTH_LOGIN_LOCKOUT_SECONDS` (`auth_login_lockout_seconds`) | `900` | Login lockout duration after threshold breaches. |
| `API_TOKEN_LAST_USED_UPDATE_INTERVAL_SECONDS` (`api_token_last_used_update_interval_seconds`) | `300` | Minimum interval between `last_used_at` writes per API token. |
| `CORS_ORIGINS` (`cors_origins`) | `http://localhost:3000,http://127.0.0.1:3000` | Allowed browser origins. Supports CSV parsing. |

## Production Validation Rules

When `APP_ENV` is `production` or `prod`:

- `JWT_SECRET` must not be `change-me` and must be at least 32 chars.
- `ADMIN_PASSWORD` must not remain `admin123`.

## Frontend Runtime Values (`web/src/api/client.ts`)

| Key | Value | Purpose |
|---|---|---|
| `DEFAULT_API_BASE_URL` | Dev: `http(s)://<host>:8000`; Prod: `/api` | API base URL fallback. |
| `API_BASE_URL` | `VITE_API_BASE_URL` or fallback | Effective API base URL. |
| `REQUEST_TIMEOUT_MS` | `VITE_API_TIMEOUT_MS` or `15000` | Fetch timeout in milliseconds. |
| `tokenStorageKey` | `threatlens.token` | Browser localStorage key for bearer token. |

## Theme Storage

`web/src/components/ThemeContext.tsx`:

- Storage key: `threatlens.theme`
- Light mode value: `light`
- Dark theme values:
  - `dark-emerald`
  - `dark-cobalt`
  - `dark-slate`
  - `dark-carbon`
  - `dark-amber`
  - `dark-crimson`
  - `dark-violet`
  - `dark-ice`
  - `dark-forest`
  - `dark-solarized`

## Web Proxy Behavior (`web/nginx/default.conf`)

- `/api/*` is reverse proxied to `http://api:8000/`.
- All other paths fall back to `/index.html` for SPA routing.

## Celery Scheduling (`backend/app/tasks/celery_app.py`)

Beat schedules:

- `dispatch-due-feeds`: every `60.0` seconds
- `dispatch-unclassified-items`: every `300.0` seconds
- `dispatch-items-missing-iocs`: every `300.0` seconds
- `dispatch-feed-metadata-backfill`: every `600.0` seconds
