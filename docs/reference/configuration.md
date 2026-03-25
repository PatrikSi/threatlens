# Configuration and Deployment

## Runtime Topology

`docker-compose.yml` defines these services:

- `db`: PostgreSQL 16 (`5432`)
- `redis`: Redis 7 (`6379`)
- `api`: FastAPI (`8000`)
- `worker`: Celery worker with embedded beat scheduler
- `web`: Nginx serving Vite build (`3000`)

## Backend Settings (`backend/app/core/config.py`)

`Settings` is loaded from process environment first, then `.env` (via `pydantic-settings`), with these defaults:

| Variable | Default | Purpose |
|---|---:|---|
| `APP_ENV` (`app_env`) | `development` | Environment mode, drives production validation rules. |
| `DATABASE_URL` (`database_url`) | `postgresql+psycopg://postgres:postgres@db:5432/threatlens` | SQLAlchemy database URL. |
| `REDIS_URL` (`redis_url`) | `redis://redis:6379/0` | Celery broker/result backend and worker coordination. |
| `JWT_SECRET` (`jwt_secret`) | `change-me` | JWT signing key. |
| `JWT_ALGORITHM` (`jwt_algorithm`) | `HS256` | JWT signature algorithm. |
| `JWT_EXPIRES_MINUTES` (`jwt_expires_minutes`) | `1440` | Access token TTL in minutes. |
| `ALLOW_LEGACY_UNSCOPED_TOKENS` (`allow_legacy_unscoped_tokens`) | `false` | Whether API tokens with empty scope lists are accepted. |
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
| `AUTH_LOGIN_MAX_ATTEMPTS` (`auth_login_max_attempts`) | `8` | Failed login attempts allowed in window before temporary lockout. |
| `AUTH_LOGIN_WINDOW_SECONDS` (`auth_login_window_seconds`) | `300` | Sliding window for failed login attempt counting. |
| `AUTH_LOGIN_LOCKOUT_SECONDS` (`auth_login_lockout_seconds`) | `900` | Login lockout duration after threshold breaches. |
| `API_TOKEN_LAST_USED_UPDATE_INTERVAL_SECONDS` (`api_token_last_used_update_interval_seconds`) | `300` | Minimum interval between `last_used_at` writes per API token. |
| `CORS_ORIGINS` (`cors_origins`) | `http://localhost:3000,http://127.0.0.1:3000` | Allowed browser origins. Supports CSV parsing. |
| `TRUSTED_PROXY_CIDRS` (`trusted_proxy_cidrs`) | _(empty)_ | Trusted proxy CIDRs permitted to provide `X-Forwarded-For`. |
| `AUTH_COOKIE_NAME` (`auth_cookie_name`) | `threatlens_session` | HttpOnly auth cookie name for browser sessions. |
| `AUTH_COOKIE_SECURE` (`auth_cookie_secure`) | `false` | Require HTTPS for session cookie. Must be `true` in production. |
| `AUTH_COOKIE_SAMESITE` (`auth_cookie_samesite`) | `lax` | SameSite mode for auth and CSRF cookies. |
| `AUTH_CSRF_COOKIE_NAME` (`auth_csrf_cookie_name`) | `threatlens_csrf` | CSRF cookie name. |
| `AUTH_CSRF_HEADER_NAME` (`auth_csrf_header_name`) | `x-csrf-token` | Header name expected on mutating requests. |
| `AUTH_REQUIRE_CSRF` (`auth_require_csrf`) | `true` | Enables CSRF verification for cookie-authenticated mutating requests. |
| `PROBE_FEED_METADATA_ON_CREATE` (`probe_feed_metadata_on_create`) | `false` | Optional synchronous metadata probing during feed create. |
| `PROBE_FEED_METADATA_ON_IMPORT` (`probe_feed_metadata_on_import`) | `false` | Optional synchronous metadata probing during feed import. |
| `MAX_METADATA_BACKFILL_TASKS_PER_REQUEST` (`max_metadata_backfill_tasks_per_request`) | `100` | Cap for metadata backfill tasks queued per request. |
| `DISPATCH_DUE_FEEDS_BATCH_SIZE` (`dispatch_due_feeds_batch_size`) | `500` | Max due feeds queued each beat cycle. |
| `DISPATCH_UNCLASSIFIED_ITEMS_BATCH_SIZE` (`dispatch_unclassified_items_batch_size`) | `200` | Max unclassified items queued each beat cycle. |
| `DISPATCH_ITEMS_MISSING_IOCS_BATCH_SIZE` (`dispatch_items_missing_iocs_batch_size`) | `200` | Max IOC-backfill items queued each beat cycle. |
| `DISPATCH_FEED_METADATA_SCAN_LIMIT` (`dispatch_feed_metadata_scan_limit`) | `250` | Feed scan cap for metadata backfill beat cycle. |
| `DISPATCH_FEED_METADATA_QUEUE_LIMIT` (`dispatch_feed_metadata_queue_limit`) | `50` | Queue cap for metadata backfill beat cycle. |
| `ALERT_MATCHES_KEYWORD_CAP` (`alert_matches_keyword_cap`) | `512` | Upper bound on distinct keywords considered in alert matching. |
| `STATS_TOP_DOMAINS_LIMIT` (`stats_top_domains_limit`) | `10` | Number of top domains returned in stats overview. |
| `RUN_MIGRATIONS_ON_STARTUP` (`run_migrations_on_startup`) | `true` | Controls automatic migration execution in `start-api.sh`. |
| `SEED_ADMIN_ON_STARTUP` (`seed_admin_on_startup`) | `true` | Controls automatic admin seeding in `start-api.sh`. |
| `SEED_ADMIN_FORCE_ROLE` (`seed_admin_force_role`) | `false` | Forces existing admin email user role to `admin` during seeding. |
| `SEED_ADMIN_REACTIVATE_EXISTING` (`seed_admin_reactivate_existing`) | `false` | Reactivates existing admin email user during seeding. |
| `SEED_ADMIN_RESET_PASSWORD_ON_STARTUP` (`seed_admin_reset_password_on_startup`) | `false` | Resets existing admin email user password to `ADMIN_PASSWORD` during seeding. |
| `LOG_LEVEL` (`log_level`) | `INFO` | Application log verbosity. |
| `HEALTH_WORKER_PING_TIMEOUT_SECONDS` (`health_worker_ping_timeout_seconds`) | `1.0` | Timeout for Celery worker ping checks on `/health/worker`. |
| `BEAT_HEARTBEAT_KEY` (`beat_heartbeat_key`) | `threatlens:beat:heartbeat` | Redis key where beat writes heartbeat timestamps. |
| `BEAT_HEARTBEAT_TTL_SECONDS` (`beat_heartbeat_ttl_seconds`) | `180` | Redis TTL for beat heartbeat key. |
| `BEAT_HEARTBEAT_STALE_AFTER_SECONDS` (`beat_heartbeat_stale_after_seconds`) | `180` | Max allowed age for beat heartbeat before `/health/beat` fails. |
| `BEAT_HEARTBEAT_INTERVAL_SECONDS` (`beat_heartbeat_interval_seconds`) | `60` | Beat schedule interval for heartbeat task emission. |

## Production Validation Rules

When `APP_ENV` is `production` or `prod`:

- `JWT_SECRET` must not be `change-me` and must be at least 32 chars.
- `ADMIN_PASSWORD` must not remain `admin123`.
- `AUTH_COOKIE_SECURE` must be `true`.

## Compose Notes

- `docker-compose.yml` runs migrations in `start-api.sh` before `uvicorn` starts serving traffic.
- `worker` depends on API health so it starts only after DB, Redis, and startup migrations are ready.
- The same compose injects `TRUSTED_PROXY_CIDRS=127.0.0.1/32,::1/128,172.16.0.0/12` by default for local reverse-proxy deployments.

## Frontend Runtime Values (`web/src/api/client.ts`)

| Key | Value | Purpose |
|---|---|---|
| `DEFAULT_API_BASE_URL` | Dev: `http(s)://<host>:8000`; Prod: `/api` | API base URL fallback. |
| `API_BASE_URL` | `VITE_API_BASE_URL` or fallback | Effective API base URL. |
| `REQUEST_TIMEOUT_MS` | `VITE_API_TIMEOUT_MS` or `15000` | Fetch timeout in milliseconds. |
| `CSRF_COOKIE_NAME` | `VITE_CSRF_COOKIE_NAME` or `threatlens_csrf` | CSRF cookie key used by frontend fetch wrapper. |
| `CSRF_HEADER_NAME` | `VITE_CSRF_HEADER_NAME` or `x-csrf-token` | CSRF header sent on mutating requests. |

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
- `record-beat-heartbeat`: every `BEAT_HEARTBEAT_INTERVAL_SECONDS`
