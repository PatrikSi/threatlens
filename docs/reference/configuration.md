# Configuration and Deployment

## Runtime Topology

`docker-compose.yml` defines these services:

- `db`: PostgreSQL 16 (`5432`)
- `redis`: Redis 7 (`6379`)
- `api`: FastAPI (internal only on `8000`)
- `worker`: Celery worker for ingestion, processing, notification, maintenance, and AI queues
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
| `DATABASE_URL` (`database_url`) | `postgresql+psycopg://postgres:postgres@db:5432/threatlens` | SQLAlchemy database URL. This code default is development-only; production rejects the default `postgres:postgres` credential pair. The bundled compose stack and generated env use the `threatlens` database role instead. |
| `REDIS_URL` (`redis_url`) | `redis://redis:6379/0` | Celery broker/result backend and worker coordination. This code default is development-only; production requires a password-bearing Redis URL. |
| `POSTGRES_PASSWORD` (`postgres_password`) | _(empty)_ | Postgres service password used by the bundled compose stack. Production requires an explicit non-default value. |
| `REDIS_PASSWORD` (`redis_password`) | _(empty)_ | Redis service password used by the bundled compose stack. Production requires an explicit non-default value. |
| `JWT_SECRET` (`jwt_secret`) | _(empty)_ | JWT signing key. In non-production, missing or placeholder values fall back to a deterministic development-only secret derived from the local runtime settings; production requires an explicit strong value. |
| `APP_DATA_ENCRYPTION_KEY` (`app_data_encryption_key`) | _(empty)_ | Dedicated secret used for encrypting stored webhook/request secrets and previews at rest. Keep distinct from `JWT_SECRET`. In non-production, missing or placeholder values fall back to a deterministic development-only key derived from the local runtime settings unless `REQUIRE_EXPLICIT_DATA_ENCRYPTION_KEY=true`; production requires an explicit strong value. |
| `APP_DATA_ENCRYPTION_PREVIOUS_KEYS` (`app_data_encryption_previous_keys`) | _(empty)_ | Optional comma-separated decryption fallback keys for data-encryption rotation and legacy ciphertext migration. Preserve these with backups until all old ciphertext has been rotated. |
| `REQUIRE_EXPLICIT_DATA_ENCRYPTION_KEY` (`require_explicit_data_encryption_key`) | `false` | Forces `APP_DATA_ENCRYPTION_KEY` to be explicitly set even outside production. Use this for any deployment with durable volumes, backups, or long-lived test data. |
| `JWT_ALGORITHM` (`jwt_algorithm`) | `HS256` | JWT signature algorithm. |
| `JWT_EXPIRES_MINUTES` (`jwt_expires_minutes`) | `1440` | Access token TTL in minutes. |
| `ALLOW_LEGACY_UNSCOPED_TOKENS` (`allow_legacy_unscoped_tokens`) | `false` | Whether API tokens with empty scope lists are accepted. |
| `ALLOW_SELF_REGISTRATION` (`allow_self_registration`) | `false` | Enables/disables `/auth/register`. |
| `DEFAULT_API_TOKEN_EXPIRY_DAYS` (`default_api_token_expiry_days`) | `90` | Default token lifetime if not supplied. |
| `AI_ENABLED` (`ai_enabled`) | `false` | Enables AI routes, nav visibility, enrichment, and daily-brief features. |
| `AI_API_KEY` (`ai_api_key`) | _(empty)_ | Optional bearer key for the configured AI endpoint. May remain blank for local unauthenticated OpenAI-compatible endpoints. |
| `EXPOSE_API_DOCS_IN_PRODUCTION` (`expose_api_docs_in_production`) | `false` | Keeps `/docs` and `/redoc` disabled by default in production. |
| `EXPOSE_OPENAPI_SCHEMA_IN_PRODUCTION` (`expose_openapi_schema_in_production`) | `true` | Keeps the machine-readable OpenAPI contract available at `/openapi.json` by default. Set to `false` if the schema is distributed only as a checked-in artifact. |
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
| `NOTIFICATION_WEBHOOK_ALLOWED_HOSTS` (`notification_webhook_allowed_hosts`) | _(empty)_ | Comma-separated exact hosts, exact `host:port` pairs, wildcard subdomains, or full `http(s)` URL prefixes that users may target for create/update/test/retry webhook operations. Host-only entries default to `https` on port `443`; entries with explicit ports or path prefixes must match exactly, `*.suffix` only matches subdomains, and allowlist entries do not support embedded credentials, query strings, or fragments. When empty, webhook egress is disabled unless `NOTIFICATION_WEBHOOK_ALLOW_ADMIN_UNRESTRICTED=true` is set for admin-managed destinations. |
| `NOTIFICATION_WEBHOOK_ALLOW_ADMIN_UNRESTRICTED` (`notification_webhook_allow_admin_unrestricted`) | `false` | Allows admins to create, update, test, retry, and deliver webhook targets outside `NOTIFICATION_WEBHOOK_ALLOWED_HOSTS`. Leave `false` for shared or internet-exposed deployments. |
| `OUTBOUND_MAX_REDIRECTS` (`outbound_max_redirects`) | `5` | Redirect hop cap for outbound fetches. |
| `PER_DOMAIN_CONCURRENCY` (`per_domain_concurrency`) | `2` | Redis-coordinated per-domain concurrent article fetch cap. |
| `AUTH_LOGIN_MAX_ATTEMPTS` (`auth_login_max_attempts`) | `8` | Failed login attempts allowed in window before temporary lockout. |
| `AUTH_LOGIN_WINDOW_SECONDS` (`auth_login_window_seconds`) | `300` | Sliding window for failed login attempt counting. |
| `AUTH_LOGIN_LOCKOUT_SECONDS` (`auth_login_lockout_seconds`) | `900` | Login lockout duration after threshold breaches. |
| `API_TOKEN_LAST_USED_UPDATE_INTERVAL_SECONDS` (`api_token_last_used_update_interval_seconds`) | `300` | Minimum interval between `last_used_at` writes per API token. |
| `CORS_ORIGINS` (`cors_origins`) | `http://localhost:3000,http://127.0.0.1:3000` | Allowed browser origins. Supports CSV parsing. |
| `TRUSTED_PROXY_CIDRS` (`trusted_proxy_cidrs`) | _(empty)_ | Trusted proxy CIDRs permitted to append `X-Forwarded-For`. Leave empty unless the API is behind a reverse proxy whose container or network CIDR you explicitly control; broad Docker bridge or private-network ranges let sibling containers spoof client IPs. |
| `ALLOWED_HOSTS` (`allowed_hosts`) | `api,localhost,127.0.0.1,::1` | Backend Host header allowlist enforced by FastAPI. Add public hostnames when exposing the API service directly or behind a proxy that preserves the public Host header. |
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
| `DISPATCH_ITEMS_MISSING_AI_ENRICHMENT_BATCH_SIZE` (`dispatch_items_missing_ai_enrichment_batch_size`) | `200` | Max recent AI enrichment repair items queued each beat cycle. |
| `DISPATCH_ITEMS_FAILED_AI_ENRICHMENT_AFTER_SECONDS` (`dispatch_items_failed_ai_enrichment_after_seconds`) | `3600` | Grace period before failed AI enrichment rows become eligible for automatic retry. |
| `AI_AUTO_ENRICH_NEW_ITEM_MAX_AGE_HOURS` (`ai_auto_enrich_new_item_max_age_hours`) | `24` | Automatic AI enrichment only queues items whose feed `published_at` and local `first_seen_at` are both inside this window; use manual AI reprocess for older backfills. |
| `AI_DAILY_BRIEF_SOURCE_AUDIT_LIMIT` (`ai_daily_brief_source_audit_limit`) | `500` | Max daily-brief candidate source rows loaded, prompted, and stored for source audit trails before applying the smaller brief item cap. |
| `DISPATCH_FEED_METADATA_SCAN_LIMIT` (`dispatch_feed_metadata_scan_limit`) | `250` | Feed scan cap for metadata backfill beat cycle. |
| `DISPATCH_FEED_METADATA_QUEUE_LIMIT` (`dispatch_feed_metadata_queue_limit`) | `50` | Queue cap for metadata backfill beat cycle. |
| `DISPATCH_AI_REPROCESS_BATCH_SIZE` (`dispatch_ai_reprocess_batch_size`) | `100` | Max AI reprocess items queued in one batch. |
| `ALERT_MATCHES_KEYWORD_CAP` (`alert_matches_keyword_cap`) | `512` | Upper bound on distinct keywords considered in alert matching. |
| `STATS_TOP_DOMAINS_LIMIT` (`stats_top_domains_limit`) | `10` | Number of top domains returned in stats overview. |
| `RUN_MIGRATIONS_ON_STARTUP` (`run_migrations_on_startup`) | `false` | Controls automatic migration execution in `start-api.sh`; the default compose overrides this to `true` for the API container. |
| `SEED_ADMIN_ON_STARTUP` (`seed_admin_on_startup`) | `false` | Controls automatic admin seeding in `start-api.sh`; the default compose passes this through to the API container and keeps it disabled on worker/beat. |
| `SEED_ADMIN_FORCE_ROLE` (`seed_admin_force_role`) | `false` | Forces existing admin email user role to `admin` during seeding. |
| `SEED_ADMIN_REACTIVATE_EXISTING` (`seed_admin_reactivate_existing`) | `false` | Reactivates existing admin email user during seeding. |
| `SEED_ADMIN_RESET_PASSWORD_ON_STARTUP` (`seed_admin_reset_password_on_startup`) | `false` | Resets existing admin email user password to `ADMIN_PASSWORD` during seeding. Leave disabled except for an intentional one-time reset. |
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
- `DATABASE_URL` must not use the default `postgres:postgres` credentials.
- `POSTGRES_PASSWORD` must be set to a non-default value.
- `REDIS_URL` must include a non-default password.
- `REDIS_PASSWORD` must be set to a non-default value.
- `AUTH_COOKIE_SECURE` must be `true`.
- `/docs` and `/redoc` are hidden by default unless `EXPOSE_API_DOCS_IN_PRODUCTION=true`.
- `/openapi.json` remains available by default so the machine-readable API contract is published. Set `EXPOSE_OPENAPI_SCHEMA_IN_PRODUCTION=false` to serve the contract only from the checked-in `docs/reference/openapi.json` artifact.

Outside production:

- `REQUIRE_EXPLICIT_DATA_ENCRYPTION_KEY=true` still forces `APP_DATA_ENCRYPTION_KEY` to be explicitly set before startup.

## Compose Notes

- `docker-compose.yml` can read a real `.env` file or pasteable YAML environment mappings generated by `./bootstrap.sh --print-compose-env`.
- For a local first run, `./bootstrap.sh` generates `.env` with fresh random secrets, HTTP-friendly local settings, and one-time admin seeding enabled.
- For Portainer, run `./bootstrap.sh --print-compose-env`, then replace the `x-db-environment`, `x-redis-environment`, and `x-backend-environment` blocks at the top of the compose file with the generated YAML mapping before deploying.
- If Postgres logs `Role "threatlens" does not exist`, the `postgres_data` volume was initialized before the matching `.env` values were present. For a disposable local install, run `docker compose down -v` and start again.
- The default ThreatLens application images point at GitHub Container Registry:
  - `ghcr.io/patriksi/threatlens-backend:${THREATLENS_IMAGE_TAG:-latest}` for `api`, `worker`, and `beat`
  - `ghcr.io/patriksi/threatlens-web:${THREATLENS_IMAGE_TAG:-latest}` for `web`
- The default compose file pulls fresh ThreatLens application images during `docker compose up`. Source builds require the explicit override: `docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build`.
- `THREATLENS_IMAGE_TAG` defaults to `latest`, which tracks the newest default published image. Set it to an immutable release tag such as `1.0.0` or `v1.0.0`, or to a `sha-<commit>` tag, when you need a pinned deployment.
- `POSTGRES_PASSWORD`, `REDIS_PASSWORD`, `DATABASE_URL`, and `REDIS_URL` are required by compose interpolation unless the generated YAML mapping is pasted into the compose file, so missing values fail the stack instead of silently falling back to weak defaults.
- `docker-compose.yml` runs migrations on API startup by default and can seed the admin account from the API container when `SEED_ADMIN_ON_STARTUP=true`.
- On first boot, either set `SEED_ADMIN_ON_STARTUP=true` for the API service or run `docker compose exec api python -m app.scripts.seed_admin` after migrations, then keep `SEED_ADMIN_ON_STARTUP=false` and `SEED_ADMIN_RESET_PASSWORD_ON_STARTUP=false` for steady state.
- `worker` and `beat` depend on healthy `api`, plus healthy DB/Redis, so they start only after schema startup work completes.
- `beat` runs as a dedicated scheduler service so periodic jobs do not multiply with worker replicas.
- `worker` consumes the `default`, `ingest`, `processing`, `notifications`, `maintenance`, and `ai` queues.
- The API is not published on a host port by default; use the web service at `http://localhost:3000/api/v1/*` or place the stack behind your own reverse proxy.
- The published OpenAPI schema is exposed through the web proxy at `http://localhost:3000/api/openapi.json`.
- The same compose injects secure defaults for `APP_ENV`, `AUTH_COOKIE_SECURE`, `AUTH_REQUIRE_CSRF`, and `REQUIRE_EXPLICIT_DATA_ENCRYPTION_KEY=true`. It intentionally lets Docker allocate project-scoped networks so multiple stacks do not collide. Set `TRUSTED_PROXY_CIDRS` only when you need the API to trust `X-Forwarded-For` from exact reverse-proxy hops you control.
- `docker-compose.build.yml` forwards exported `APP_VERSION`, `BUILD_DATE`, and `VCS_REF` values into every locally built ThreatLens image as OCI label args. Export them before running the source-build override if you want local image metadata to capture the app version, checked-out revision, and build time; otherwise `APP_VERSION` falls back to the checked-in compose default and the provenance labels fall back to `unknown`.
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

- Feed and article fetches, AI provider calls, and notification webhooks are separate outbound trust boundaries with separate deny-by-default private-network controls (`ALLOW_PRIVATE_NETWORK_FETCH`, `ALLOW_PRIVATE_NETWORK_AI`, `ALLOW_PRIVATE_NETWORK_WEBHOOKS`).
- `NOTIFICATION_WEBHOOK_ALLOWED_HOSTS` is the webhook egress allowlist. ThreatLens reevaluates queued webhook deliveries against the current allowlist before sending, so tightening the list also blocks older deliveries. Admin-managed destinations can bypass the allowlist only when `NOTIFICATION_WEBHOOK_ALLOW_ADMIN_UNRESTRICTED=true`; analyst-managed destinations must always match the allowlist. Host-only entries approve the default `https` origin, exact `host:port` or full URL prefix entries can pin non-default ports and tenant path prefixes, and wildcard entries do not cover the apex domain.
- `TRUSTED_PROXY_CIDRS` only controls whether ThreatLens trusts proxy-supplied client IP headers. It does not widen outbound allowlists, and every trusted proxy hop that can append `X-Forwarded-For` should be included.
- `APP_DATA_ENCRYPTION_KEY` protects feed URLs, stored webhook templates, and saved delivery snapshots at rest; keep it distinct from `JWT_SECRET` and back it up with any `APP_DATA_ENCRYPTION_PREVIOUS_KEYS`.
- Admin-only encrypted data inventory is available at `/health/encrypted-data` and includes both a current scan and the most recent startup scan summary for unreadable encrypted rows.

## Theme Storage

`web/src/components/ThemeContext.tsx`:

- Storage key: `threatlens.theme`
- Light mode value: `light`
- Dark mode value: `dark`
- Legacy stored values beginning with `dark-` or `theme-dark` are normalized to `dark` on load.
- The static `/theme-init.js` bootstrap applies `theme-light` or `theme-dark` before the React bundle loads so persisted dark mode does not flash through the light palette.

## Web Proxy Behavior (`web/nginx/default.conf.template`)

- `/api/v1/*` is reverse proxied to `http://api:8000/v1/*`.
- `/api/openapi.json` proxies the live OpenAPI schema from the backend service root.
- Other `/api/*` paths return `404` in the bundled web image.
- All other paths fall back to `/index.html` for SPA routing.
- `/`, `/index.html`, and SPA fallback responses use `Cache-Control: no-store` so browser sessions pick up newly deployed bundles after container updates.
- `/theme-init.js` uses `Cache-Control: no-cache, max-age=0, must-revalidate` because it is a stable URL outside the hashed asset pipeline.
- `/assets/*` uses `Cache-Control: public, max-age=31536000, immutable`; Vite emits hashed filenames, so changed assets receive new URLs.
- The web app CSP and `X-Frame-Options: DENY` header are applied only to SPA/static responses, not proxied API responses.
- `THREATLENS_CSP_CONNECT_SRC` defaults to `'self'` in the web container image.
- `THREATLENS_CSP_FRAME_SRC` defaults to `'self'` so dashboard original-article previews can embed the backend-fetched preview endpoint.
- For non-proxied deployments, override `THREATLENS_CSP_CONNECT_SRC` to include the external API origin used by `VITE_API_BASE_URL`.
- The article preview endpoint fetches source HTML server-side, strips active content, and serves it with a dedicated sandboxing CSP. Publisher-side `X-Frame-Options` or `frame-ancestors` policies do not apply to that ThreatLens-hosted snapshot.

## Celery Scheduling (`backend/app/tasks/celery_app.py`)

Beat schedules:

- `dispatch-due-feeds`: every `60.0` seconds
- `dispatch-unclassified-items`: every `300.0` seconds
- `dispatch-items-missing-iocs`: every `300.0` seconds
- `dispatch-feed-metadata-backfill`: every `600.0` seconds
- `dispatch-daily-digest-notifications`: every `3600.0` seconds
- `dispatch-daily-ai-brief-generation`: every `300.0` seconds
- `record-beat-heartbeat`: every `BEAT_HEARTBEAT_INTERVAL_SECONDS`
