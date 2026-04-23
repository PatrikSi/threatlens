# Configuration and Deployment

## Runtime Topology

`docker-compose.yml` defines these services:

- `db`: PostgreSQL 16 (`5432`)
- `redis`: Redis 7 (`6379`)
- `api`: FastAPI (internal only on `8000`)
- `worker`: Celery worker
- `beat`: Celery beat scheduler
- `web`: Nginx serving Vite build (`3000`) and reverse proxying only `/api/v1/*` plus `/api/openapi.json` to `api`

## Published HTTP Paths

- Browser/API base through the bundled web proxy: `/api/v1`
- OpenAPI schema through the bundled web proxy: `/api/openapi.json`
- Internal backend service versioned base: `/v1`
- Compatibility aliases on the backend service root are intentionally outside the published contract, excluded from the OpenAPI schema, and not exposed through the bundled web proxy

## Backend Settings (`backend/app/core/config.py`)

`Settings` is loaded from process environment first, then `.env` (via `pydantic-settings`), with these code defaults. The shipped `.env.example` deliberately overrides some of them with more production-oriented values for the default compose stack.

| Variable | Default | Purpose |
|---|---:|---|
| `APP_ENV` (`app_env`) | `development` | Environment mode, drives production validation rules. |
| `DATABASE_URL` (`database_url`) | `postgresql+psycopg://postgres:postgres@db:5432/threatlens` | SQLAlchemy database URL. |
| `REDIS_URL` (`redis_url`) | `redis://redis:6379/0` | Celery broker/result backend and worker coordination. |
| `JWT_SECRET` (`jwt_secret`) | _(empty)_ | JWT signing key. In non-production, missing or placeholder values are replaced with a runtime-generated secret; production requires an explicit strong value. |
| `APP_DATA_ENCRYPTION_KEY` (`app_data_encryption_key`) | _(empty)_ | Dedicated secret used for encrypting stored webhook/request secrets and previews at rest. Keep distinct from `JWT_SECRET`. In non-production, missing or placeholder values are replaced with a runtime-generated key; production requires an explicit strong value. |
| `APP_DATA_ENCRYPTION_PREVIOUS_KEYS` (`app_data_encryption_previous_keys`) | _(empty)_ | Optional comma-separated decryption fallback keys for data-encryption rotation and legacy ciphertext migration. |
| `JWT_ALGORITHM` (`jwt_algorithm`) | `HS256` | JWT signature algorithm. |
| `JWT_EXPIRES_MINUTES` (`jwt_expires_minutes`) | `1440` | Access token TTL in minutes. |
| `ALLOW_LEGACY_UNSCOPED_TOKENS` (`allow_legacy_unscoped_tokens`) | `false` | Whether API tokens with empty scope lists are accepted. |
| `ALLOW_SELF_REGISTRATION` (`allow_self_registration`) | `false` | Enables/disables `/auth/register`. |
| `DEFAULT_API_TOKEN_EXPIRY_DAYS` (`default_api_token_expiry_days`) | `90` | Default token lifetime if not supplied. |
| `AI_ENABLED` (`ai_enabled`) | `false` | Enables AI routes, nav visibility, enrichment, and daily-brief features. |
| `AI_API_KEY` (`ai_api_key`) | _(empty)_ | Optional bearer key for the configured AI endpoint. May remain blank for local unauthenticated OpenAI-compatible endpoints. |
| `EXPOSE_API_DOCS_IN_PRODUCTION` (`expose_api_docs_in_production`) | `false` | Keeps `/docs` and `/redoc` disabled by default in production. The OpenAPI schema remains available at `/openapi.json`. |
| `ADMIN_EMAIL` (`admin_email`) | `admin@example.com` | Seed admin identity. |
| `ADMIN_PASSWORD` (`admin_password`) | `admin123` | Seed admin password. |
| `FETCH_USER_AGENT` (`fetch_user_agent`) | `ThreatLensBot/1.0 (+https://localhost)` | User-Agent for feed/article HTTP requests. |
| `FEED_CONNECT_TIMEOUT_SECONDS` (`feed_connect_timeout_seconds`) | `5` | Feed HTTP connect timeout. |
| `FEED_READ_TIMEOUT_SECONDS` (`feed_read_timeout_seconds`) | `15` | Feed HTTP read timeout. |
| `FEED_MAX_BYTES` (`feed_max_bytes`) | `2000000` | Max feed response size before rejection. |
| `ARTICLE_CONNECT_TIMEOUT_SECONDS` (`article_connect_timeout_seconds`) | `5` | Article HTTP connect timeout. |
| `ARTICLE_READ_TIMEOUT_SECONDS` (`article_read_timeout_seconds`) | `20` | Article HTTP read timeout. |
| `ARTICLE_MAX_BYTES` (`article_max_bytes`) | `4000000` | Max article response size before rejection. |
| `ALLOW_PRIVATE_NETWORK_FETCH` (`allow_private_network_fetch`) | `false` | Allows feed and article fetches to private-network or internal-only hosts when explicitly enabled. |
| `ALLOW_PRIVATE_NETWORK_AI` (`allow_private_network_ai`) | `false` | Allows AI requests to private-network or internal-only hosts when explicitly enabled. Publicly routable AI endpoints must still use `https`. |
| `ALLOW_PRIVATE_NETWORK_WEBHOOKS` (`allow_private_network_webhooks`) | `false` | Separately allows notification webhook deliveries to private-network or internal-only hosts when explicitly enabled. |
| `NOTIFICATION_WEBHOOK_ALLOWED_HOSTS` (`notification_webhook_allowed_hosts`) | _(empty)_ | Comma-separated exact hosts or `*.suffix` patterns that non-admin users may target for create/update/test/retry webhook operations. Plain host entries approve the default `https` origin for that host, explicit non-default ports are rejected, and `*.suffix` only matches subdomains. When empty, analyst-managed webhook egress is disabled and only admins can manage outbound webhook destinations. |
| `OUTBOUND_MAX_REDIRECTS` (`outbound_max_redirects`) | `5` | Redirect hop cap for outbound fetches. |
| `PER_DOMAIN_CONCURRENCY` (`per_domain_concurrency`) | `2` | Redis-coordinated per-domain concurrent article fetch cap. |
| `AUTH_LOGIN_MAX_ATTEMPTS` (`auth_login_max_attempts`) | `8` | Failed login attempts allowed in window before temporary lockout. |
| `AUTH_LOGIN_WINDOW_SECONDS` (`auth_login_window_seconds`) | `300` | Sliding window for failed login attempt counting. |
| `AUTH_LOGIN_LOCKOUT_SECONDS` (`auth_login_lockout_seconds`) | `900` | Login lockout duration after threshold breaches. |
| `API_TOKEN_LAST_USED_UPDATE_INTERVAL_SECONDS` (`api_token_last_used_update_interval_seconds`) | `300` | Minimum interval between `last_used_at` writes per API token. |
| `CORS_ORIGINS` (`cors_origins`) | `http://localhost:3000,http://127.0.0.1:3000` | Allowed browser origins. Supports CSV parsing. |
| `TRUSTED_PROXY_CIDRS` (`trusted_proxy_cidrs`) | _(empty)_ | Trusted proxy CIDRs permitted to append `X-Forwarded-For`. Include only the exact hops you control; broad Docker bridge or private-network ranges let sibling containers spoof client IPs. |
| `AUTH_COOKIE_NAME` (`auth_cookie_name`) | `threatlens_session` | HttpOnly auth cookie name for browser sessions. |
| `AUTH_COOKIE_DOMAIN` (`auth_cookie_domain`) | _(empty)_ | Optional cookie domain override for browser session and CSRF cookies. |
| `AUTH_COOKIE_PATH` (`auth_cookie_path`) | `/` | Cookie path applied to browser session and CSRF cookies. |
| `AUTH_COOKIE_SECURE` (`auth_cookie_secure`) | `false` | Require HTTPS for session cookie. Must be `true` in production. |
| `AUTH_COOKIE_SAMESITE` (`auth_cookie_samesite`) | `lax` | SameSite mode for auth and CSRF cookies. |
| `AUTH_CSRF_COOKIE_NAME` (`auth_csrf_cookie_name`) | `threatlens_csrf` | CSRF cookie name. |
| `AUTH_CSRF_HEADER_NAME` (`auth_csrf_header_name`) | `x-csrf-token` | Header name expected on mutating requests. |
| `AUTH_REQUIRE_CSRF` (`auth_require_csrf`) | `true` | Enables CSRF verification for cookie-authenticated mutating requests. |
| `PROBE_FEED_METADATA_ON_CREATE` (`probe_feed_metadata_on_create`) | `false` | Optional synchronous metadata probing during feed create. |
| `PROBE_FEED_METADATA_ON_IMPORT` (`probe_feed_metadata_on_import`) | `false` | Optional synchronous metadata probing during feed import. |
| `MAX_METADATA_BACKFILL_TASKS_PER_REQUEST` (`max_metadata_backfill_tasks_per_request`) | `100` | Cap for metadata backfill tasks queued per request. |
| `DISPATCH_DUE_FEEDS_BATCH_SIZE` (`dispatch_due_feeds_batch_size`) | `500` | Max due feeds queued each beat cycle. |
| `DISPATCH_FEED_CLAIM_SECONDS` (`dispatch_feed_claim_seconds`) | `900` | How long a due-feed dispatch claim is held before another dispatcher may reclaim it. |
| `DISPATCH_ITEMS_MISSING_ARTICLES_BATCH_SIZE` (`dispatch_items_missing_articles_batch_size`) | `200` | Max repairable article-fetch items queued each beat cycle. |
| `DISPATCH_ITEMS_MISSING_ARTICLES_AFTER_SECONDS` (`dispatch_items_missing_articles_after_seconds`) | `300` | Grace period before missing or retryable article fetches are eligible for repair dispatch. |
| `DISPATCH_UNCLASSIFIED_ITEMS_BATCH_SIZE` (`dispatch_unclassified_items_batch_size`) | `200` | Max unclassified items queued each beat cycle. |
| `DISPATCH_ITEMS_MISSING_IOCS_BATCH_SIZE` (`dispatch_items_missing_iocs_batch_size`) | `200` | Max IOC-backfill items queued each beat cycle. |
| `DISPATCH_FEED_METADATA_SCAN_LIMIT` (`dispatch_feed_metadata_scan_limit`) | `250` | Feed scan cap for metadata backfill beat cycle. |
| `DISPATCH_FEED_METADATA_QUEUE_LIMIT` (`dispatch_feed_metadata_queue_limit`) | `50` | Queue cap for metadata backfill beat cycle. |
| `DISPATCH_AI_REPROCESS_BATCH_SIZE` (`dispatch_ai_reprocess_batch_size`) | `100` | Max AI reprocess items queued in one batch. |
| `ALERT_MATCHES_KEYWORD_CAP` (`alert_matches_keyword_cap`) | `512` | Upper bound on distinct keywords considered in alert matching. |
| `STATS_TOP_DOMAINS_LIMIT` (`stats_top_domains_limit`) | `10` | Number of top domains returned in stats overview. |
| `RUN_MIGRATIONS_ON_STARTUP` (`run_migrations_on_startup`) | `false` | Controls automatic migration execution in `start-api.sh`; the default compose overrides this to `true` for the API container. |
| `SEED_ADMIN_ON_STARTUP` (`seed_admin_on_startup`) | `false` | Controls automatic admin seeding in `start-api.sh`; the default compose passes this through to the API container and keeps it disabled on worker/beat. |
| `SEED_ADMIN_FORCE_ROLE` (`seed_admin_force_role`) | `false` | Forces existing admin email user role to `admin` during seeding. |
| `SEED_ADMIN_REACTIVATE_EXISTING` (`seed_admin_reactivate_existing`) | `false` | Reactivates existing admin email user during seeding. |
| `SEED_ADMIN_RESET_PASSWORD_ON_STARTUP` (`seed_admin_reset_password_on_startup`) | `false` | Resets existing admin email user password to `ADMIN_PASSWORD` during seeding. |
| `LOG_LEVEL` (`log_level`) | `INFO` | Application log verbosity. |
| `HEALTH_WORKER_PING_TIMEOUT_SECONDS` (`health_worker_ping_timeout_seconds`) | `1.0` | Timeout for Celery worker ping checks on `/health/worker`. |
| `BEAT_HEARTBEAT_KEY` (`beat_heartbeat_key`) | `threatlens:beat:heartbeat` | Redis key where beat writes heartbeat timestamps. |
| `BEAT_HEARTBEAT_TTL_SECONDS` (`beat_heartbeat_ttl_seconds`) | `180` | Redis TTL for beat heartbeat key. |
| `BEAT_HEARTBEAT_STALE_AFTER_SECONDS` (`beat_heartbeat_stale_after_seconds`) | `180` | Max allowed age for beat heartbeat before `/health/beat` fails. |
| `BEAT_HEARTBEAT_INTERVAL_SECONDS` (`beat_heartbeat_interval_seconds`) | `60` | Beat schedule interval for heartbeat task emission. |
| `NOTIFICATION_DELIVERY_ENQUEUE_BATCH_SIZE` (`notification_delivery_enqueue_batch_size`) | `100` | Delivery batch size when queueing webhook deliveries. |
| `NOTIFICATION_DELIVERY_RECOVERY_BATCH_SIZE` (`notification_delivery_recovery_batch_size`) | `100` | Delivery batch size when retrying stale webhook deliveries. |
| `NOTIFICATION_DELIVERY_SENDING_STALE_AFTER_SECONDS` (`notification_delivery_sending_stale_after_seconds`) | `120` | Age after which in-flight webhook sends are treated as stale. |
| `NOTIFICATION_DELIVERY_QUEUE_DEGRADED_AFTER_SECONDS` (`notification_delivery_queue_degraded_after_seconds`) | `300` | Age after which queued webhook deliveries are surfaced as degraded. |

## Production Validation Rules

When `APP_ENV` is `production` or `prod`:

- `JWT_SECRET` must not be `change-me` and must be at least 32 chars.
- `APP_DATA_ENCRYPTION_KEY` must be set and be at least 32 chars.
- `ADMIN_PASSWORD` must not remain `admin123`.
- `AUTH_COOKIE_SECURE` must be `true`.
- `/docs` and `/redoc` are hidden by default unless `EXPOSE_API_DOCS_IN_PRODUCTION=true`.
- `/openapi.json` remains available so the machine-readable API contract is always published.

## Compose Notes

- `docker-compose.yml` expects a real `.env` file and is the production-oriented reference deployment.
- `docker-compose.yml` runs migrations on API startup by default and can seed the admin account from the API container when `SEED_ADMIN_ON_STARTUP=true`.
- `worker` and `beat` depend on healthy `api`, plus healthy DB/Redis, so they start only after schema startup work completes.
- `beat` runs as a dedicated scheduler service so periodic jobs do not multiply with worker replicas.
- The API is not published on a host port by default; use the web service at `http://localhost:3000/api/v1/*` or place the stack behind your own reverse proxy.
- The published OpenAPI schema is exposed through the web proxy at `http://localhost:3000/api/openapi.json`.
- The same compose injects secure defaults for `APP_ENV`, `AUTH_COOKIE_SECURE`, and `AUTH_REQUIRE_CSRF`; its shipped `TRUSTED_PROXY_CIDRS` guidance stays narrow by default, so add only the exact proxy hops you control if you want `X-Forwarded-For` preserved.
- `WEB_VITE_API_BASE_URL` from `.env` is passed to the web image as `VITE_API_BASE_URL` and defaults to `/api/v1`. For non-proxied deployments, set it to a full versioned API origin such as `https://api.example.com/v1`.

## Frontend Runtime Values (`web/src/api/client.ts`)

| Key | Value | Purpose |
|---|---|---|
| `DEFAULT_API_BASE_URL` | Dev: `http(s)://<host>:8000/v1`; Prod: `/api/v1` | API base URL fallback. |
| `API_BASE_URL` | `VITE_API_BASE_URL` or fallback | Effective API base URL. |
| `REQUEST_TIMEOUT_MS` | `VITE_API_TIMEOUT_MS` or `15000` | Fetch timeout in milliseconds. |
| `CSRF_COOKIE_NAME` | `VITE_CSRF_COOKIE_NAME` or `threatlens_csrf` | CSRF cookie key used by frontend fetch wrapper. |
| `CSRF_HEADER_NAME` | `VITE_CSRF_HEADER_NAME` or `x-csrf-token` | CSRF header sent on mutating requests. |

## Trust and Egress Notes

- Feed/article fetches, AI calls, and notification webhooks are separate outbound trust boundaries with separate private-network controls (`ALLOW_PRIVATE_NETWORK_FETCH`, `ALLOW_PRIVATE_NETWORK_AI`, `ALLOW_PRIVATE_NETWORK_WEBHOOKS`).
- `NOTIFICATION_WEBHOOK_ALLOWED_HOSTS` is the analyst webhook egress allowlist. ThreatLens reevaluates queued analyst-owned webhook deliveries against the current allowlist before sending, so tightening the list also blocks older deliveries. Host-only entries approve the default `https` origin, and wildcard entries do not cover the apex domain.
- `TRUSTED_PROXY_CIDRS` only controls whether ThreatLens trusts proxy-supplied client IP headers. It does not widen outbound allowlists, and every trusted proxy hop that can append `X-Forwarded-For` should be included.
- `APP_DATA_ENCRYPTION_KEY` protects stored webhook templates and saved delivery snapshots at rest; keep it distinct from `JWT_SECRET`.

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

## Web Proxy Behavior (`web/nginx/default.conf.template`)

- `/api/v1/*` is reverse proxied to `http://api:8000/v1/*`.
- `/api/openapi.json` proxies the live OpenAPI schema from the backend service root.
- Other `/api/*` paths return `404` in the bundled web image.
- All other paths fall back to `/index.html` for SPA routing.
- `THREATLENS_CSP_CONNECT_SRC` defaults to `'self'` in the web container image.
- For non-proxied deployments, override `THREATLENS_CSP_CONNECT_SRC` to include the external API origin used by `VITE_API_BASE_URL`.

## Celery Scheduling (`backend/app/tasks/celery_app.py`)

Beat schedules:

- `dispatch-due-feeds`: every `60.0` seconds
- `dispatch-unclassified-items`: every `300.0` seconds
- `dispatch-items-missing-iocs`: every `300.0` seconds
- `dispatch-feed-metadata-backfill`: every `600.0` seconds
- `dispatch-daily-digest-notifications`: every `3600.0` seconds
- `dispatch-daily-ai-brief-generation`: every `300.0` seconds
- `record-beat-heartbeat`: every `BEAT_HEARTBEAT_INTERVAL_SECONDS`
