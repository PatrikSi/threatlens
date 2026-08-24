# Configuration and Deployment

## Runtime Topology

`docker-compose.yml` defines these services:

- `db`: PostgreSQL 16 (`5432`)
- `redis`: Redis 7 (`6379`)
- `api`: FastAPI (internal only on `8000`)
- `worker`: Celery worker for ingestion and processing queues
- `worker-ai`: isolated Celery worker for AI enrichment, daily briefs, and report generation
- `worker-maintenance`: isolated Celery worker for scheduler heartbeats, outbox recovery, and maintenance tasks
- `worker-notifications`: isolated Celery worker for integration event routing and outbound deliveries
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
| `PUBLIC_APP_URL` (`public_app_url`) | _(empty)_ | Optional public browser URL, without credentials/query/fragment, used to make report integration links absolute. |
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
| `ALLOW_PRIVATE_NETWORK_OIDC` (`allow_private_network_oidc`) | `false` | Allows OIDC discovery, token, JWKS, and UserInfo requests to private-network or internal-only identity providers. Public endpoints must use `https`; enable this only for explicitly trusted internal IdPs. |
| `ALLOW_INSECURE_HTTP_OIDC` (`allow_insecure_http_oidc`) | `false` | Allows publicly routable OIDC endpoints and the configured ThreatLens callback origin to use plaintext `http`. Private/internal OIDC endpoints still require `ALLOW_PRIVATE_NETWORK_OIDC=true`. Keep disabled outside isolated development environments. |
| `OUTBOUND_MAX_REDIRECTS` (`outbound_max_redirects`) | `5` | Redirect hop cap for outbound fetches. |
| `PER_DOMAIN_CONCURRENCY` (`per_domain_concurrency`) | `2` | Redis-coordinated per-domain concurrent article fetch cap. |
| `AUTH_LOGIN_MAX_ATTEMPTS` (`auth_login_max_attempts`) | `8` | Failed login attempts allowed in window before temporary lockout. |
| `AUTH_LOGIN_IP_MAX_ATTEMPTS` (`auth_login_ip_max_attempts`) | `50` | Failed login attempts allowed per client IP in the same window. Must be at least `AUTH_LOGIN_MAX_ATTEMPTS`. |
| `AUTH_LOGIN_WINDOW_SECONDS` (`auth_login_window_seconds`) | `300` | Sliding window for failed login attempt counting. |
| `AUTH_LOGIN_LOCKOUT_SECONDS` (`auth_login_lockout_seconds`) | `900` | Login lockout duration after threshold breaches. |
| `REDIS_CONNECT_TIMEOUT_SECONDS` (`redis_connect_timeout_seconds`) | `2.0` | Connection timeout for Redis-backed coordination and rate-limit operations. |
| `REDIS_SOCKET_TIMEOUT_SECONDS` (`redis_socket_timeout_seconds`) | `2.0` | Socket operation timeout for Redis-backed coordination and rate-limit operations. |
| `DATABASE_CONNECT_TIMEOUT_SECONDS` (`database_connect_timeout_seconds`) | `5` | PostgreSQL connection establishment timeout. |
| `DATABASE_STATEMENT_TIMEOUT_MS` (`database_statement_timeout_ms`) | `30000` | PostgreSQL statement timeout applied to application connections. |
| `DATABASE_POOL_TIMEOUT_SECONDS` (`database_pool_timeout_seconds`) | `10` | Maximum wait for an available pooled database connection. |
| `API_TOKEN_LAST_USED_UPDATE_INTERVAL_SECONDS` (`api_token_last_used_update_interval_seconds`) | `300` | Minimum interval between `last_used_at` writes per API token. |
| `OIDC_TRANSACTION_COOKIE_NAME` (`oidc_transaction_cookie_name`) | `threatlens_oidc_transaction` | HttpOnly cookie used for the short-lived OIDC state, nonce, and PKCE transaction. |
| `OIDC_TRANSACTION_TTL_SECONDS` (`oidc_transaction_ttl_seconds`) | `600` | Maximum age of an OIDC sign-in or account-link transaction. |
| `OIDC_CALLBACK_PATH` (`oidc_callback_path`) | `/api/v1/auth/oidc/callback` | Public callback path appended to the configured ThreatLens origin. Use `/v1/auth/oidc/callback` only when exposing the API directly without the bundled web proxy. |
| `OIDC_METADATA_CACHE_SECONDS` (`oidc_metadata_cache_seconds`) | `300` | In-process cache lifetime for validated provider discovery metadata. |
| `OIDC_CONNECT_TIMEOUT_SECONDS` (`oidc_connect_timeout_seconds`) | `5` | Connect timeout for discovery, token, JWKS, and UserInfo requests. |
| `OIDC_READ_TIMEOUT_SECONDS` (`oidc_read_timeout_seconds`) | `10` | Read/write timeout for OIDC provider requests. |
| `OIDC_MAX_RESPONSE_BYTES` (`oidc_max_response_bytes`) | `1000000` | Maximum accepted response size for each OIDC provider endpoint. |
| `CORS_ORIGINS` (`cors_origins`) | `http://localhost:3000,http://127.0.0.1:3000` | Allowed browser origins. Supports CSV parsing. |
| `TRUSTED_PROXY_CIDRS` (`trusted_proxy_cidrs`) | _(empty)_ | Trusted proxy CIDRs permitted to append `X-Forwarded-For`. Leave empty unless the API is behind a reverse proxy whose container or network CIDR you explicitly control; broad Docker bridge or private-network ranges let sibling containers spoof client IPs. |
| `TRUSTED_PROXY_HOSTS` (`trusted_proxy_hosts`) | _(empty)_ | Exact trusted proxy hostnames resolved at startup into client-address networks. The bundled compose stack sets this to `web`; prefer CIDRs when addresses are stable. |
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
| `CELERY_VISIBILITY_TIMEOUT_SECONDS` (`celery_visibility_timeout_seconds`) | `3600` | Redis broker visibility timeout for unacknowledged tasks. Keep this above the longest AI report execution and its generation lease. |
| `REPORT_GENERATION_LEASE_SECONDS` (`report_generation_lease_seconds`) | `600` | Renewable database ownership lease for one report generation worker. Must be at least 360 seconds. |
| `REPORT_SCHEDULE_MAX_ATTEMPTS` (`report_schedule_max_attempts`) | `5` | Consecutive planning attempts before a transient failure is recorded as exhausted and the schedule advances. Invalid configuration failures use a smaller capped retry count before quarantine. |
| `REPORT_SCHEDULE_RETRY_BACKOFF_SECONDS` (`report_schedule_retry_backoff_seconds`) | `60` | Initial exponential delay after a report schedule planning failure. |
| `REPORT_SCHEDULE_RETRY_MAX_BACKOFF_SECONDS` (`report_schedule_retry_max_backoff_seconds`) | `3600` | Maximum report schedule planning retry delay. |
| `REPORT_DISPATCH_BATCH_SIZE` (`report_dispatch_batch_size`) | `100` | Maximum durable queued report tasks recovered in one dispatch sweep. |
| `REPORT_DISPATCH_MAX_ATTEMPTS` (`report_dispatch_max_attempts`) | `10` | Queue publication attempts allowed before a report dispatch is settled as failed and requires an explicit report retry after the queue recovers. |
| `REPORT_DISPATCH_CLAIM_SECONDS` (`report_dispatch_claim_seconds`) | `60` | Time allowed for one dispatcher to publish and record a report task before another dispatcher may reclaim the attempt. |
| `REPORT_DISPATCH_STALE_AFTER_SECONDS` (`report_dispatch_stale_after_seconds`) | `300` | Time a published report task may remain queued without a worker start before it is republished with the same stable task ID. |
| `REPORT_DISPATCH_RETRY_BACKOFF_SECONDS` (`report_dispatch_retry_backoff_seconds`) | `15` | Initial exponential delay after report queue publication fails. |
| `REPORT_DISPATCH_RETRY_MAX_BACKOFF_SECONDS` (`report_dispatch_retry_max_backoff_seconds`) | `900` | Maximum report queue publication retry delay. |
| `ALERT_MATCHES_KEYWORD_CAP` (`alert_matches_keyword_cap`) | `512` | Upper bound on distinct keywords considered in alert matching. |
| `STATS_TOP_DOMAINS_LIMIT` (`stats_top_domains_limit`) | `10` | Number of top domains returned in stats overview. |
| `RUN_MIGRATIONS_ON_STARTUP` (`run_migrations_on_startup`) | `false` | Controls automatic migration execution in `start-api.sh`; the default compose overrides this to `true` for the API container. |
| `SEED_ADMIN_ON_STARTUP` (`seed_admin_on_startup`) | `false` | Controls automatic admin seeding in `start-api.sh`; the default compose passes this through to the API container and keeps it disabled on worker/beat. |
| `SEED_ADMIN_FORCE_ROLE` (`seed_admin_force_role`) | `false` | Forces existing admin email user role to `admin` during seeding. |
| `SEED_ADMIN_REACTIVATE_EXISTING` (`seed_admin_reactivate_existing`) | `false` | Reactivates existing admin email user during seeding. |
| `SEED_ADMIN_RESET_PASSWORD_ON_STARTUP` (`seed_admin_reset_password_on_startup`) | `false` | Resets existing admin email user password to `ADMIN_PASSWORD` during seeding. Leave disabled except for an intentional one-time reset. |
| `LOG_LEVEL` (`log_level`) | `INFO` | Shared API, worker, and Beat threshold: `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL`. Invalid values fail startup. |
| `LOG_LEVEL_OVERRIDES` (`log_level_overrides`) | empty | Optional comma-separated `logger.name=LEVEL` overrides for focused diagnostics without raising every dependency to `DEBUG`. Invalid entries fail startup. |
| `LOG_FORMAT` (`log_format`) | `text` | `text` for human-readable console logs or newline-delimited `json` for log collectors. |
| `LOG_DETAIL` (`log_detail`) | `standard` | `verbose` adds safe request-start diagnostics and debug lifecycle events when `LOG_LEVEL=DEBUG`; bodies and credentials remain excluded. |
| `LOG_INCLUDE_CLIENT_IP` (`log_include_client_ip`) | `false` | Include the direct client address in request logs. This may be personal data. |
| `LOG_SLOW_REQUEST_MS` (`log_slow_request_ms`) | `1000` | Promote successful requests at or above this duration to warning logs. |
| `LOG_MAX_EVENT_CHARS` (`log_max_event_chars`) | `20000` | Per-message and exception text bound before diagnostic output is truncated. |
| `LOG_SQL` (`log_sql`) | `false` | Emit SQLAlchemy statements at `INFO`; bound parameter values are always hidden. |
| `HEALTH_WORKER_PING_TIMEOUT_SECONDS` (`health_worker_ping_timeout_seconds`) | `1.0` | Timeout for Celery worker ping checks on `/health/worker`. |
| `BEAT_HEARTBEAT_KEY` (`beat_heartbeat_key`) | `threatlens:beat:heartbeat` | Redis key where the Beat-to-worker heartbeat task writes timestamps. |
| `BEAT_SCHEDULER_HEARTBEAT_KEY` (`beat_scheduler_heartbeat_key`) | `threatlens:beat:scheduler-heartbeat` | Redis key updated directly after each successful Celery Beat scheduler tick. |
| `BEAT_HEARTBEAT_TTL_SECONDS` (`beat_heartbeat_ttl_seconds`) | `180` | Redis TTL for both scheduler and Beat-to-worker heartbeat keys. |
| `BEAT_HEARTBEAT_STALE_AFTER_SECONDS` (`beat_heartbeat_stale_after_seconds`) | `180` | Max allowed age for both heartbeats; the round trip controls API readiness and the direct scheduler heartbeat controls watchdog recovery. |
| `BEAT_HEARTBEAT_INTERVAL_SECONDS` (`beat_heartbeat_interval_seconds`) | `60` | Beat schedule interval for heartbeat task emission. |
| `BEAT_WATCHDOG_STARTUP_GRACE_SECONDS` (`beat_watchdog_startup_grace_seconds`) | `240` | Grace period after Beat starts before a missing or stale heartbeat forces a restart. |
| `BEAT_WATCHDOG_CHECK_INTERVAL_SECONDS` (`beat_watchdog_check_interval_seconds`) | `15` | Interval between watchdog heartbeat checks. |
| `BEAT_WATCHDOG_TERMINATE_TIMEOUT_SECONDS` (`beat_watchdog_terminate_timeout_seconds`) | `10` | Time allowed for Beat to stop before the watchdog force-kills it. |
| `NOTIFICATION_DELIVERY_ENQUEUE_BATCH_SIZE` (`notification_delivery_enqueue_batch_size`) | `100` | Delivery batch size when queueing webhook deliveries. |
| `NOTIFICATION_DELIVERY_RECOVERY_BATCH_SIZE` (`notification_delivery_recovery_batch_size`) | `100` | Delivery batch size when retrying stale webhook deliveries. |
| `NOTIFICATION_DELIVERY_SENDING_STALE_AFTER_SECONDS` (`notification_delivery_sending_stale_after_seconds`) | `120` | Age after which in-flight webhook sends are treated as stale. |
| `NOTIFICATION_DELIVERY_QUEUE_DEGRADED_AFTER_SECONDS` (`notification_delivery_queue_degraded_after_seconds`) | `300` | Age after which queued webhook deliveries are surfaced as degraded. |
| `NOTIFICATION_DELIVERY_RETRY_MAX_ATTEMPTS` (`notification_delivery_retry_max_attempts`) | `3` | Maximum automatic retry attempts for legacy notification webhook deliveries. |
| `NOTIFICATION_DELIVERY_RETRY_BACKOFF_SECONDS` (`notification_delivery_retry_backoff_seconds`) | `30` | Initial retry delay for legacy notification webhook deliveries. |
| `INTEGRATION_EVENT_ROUTING_BATCH_SIZE` (`integration_event_routing_batch_size`) | `200` | Maximum outbox events recovered per routing sweep. |
| `INTEGRATION_EVENT_ROUTING_STALE_AFTER_SECONDS` (`integration_event_routing_stale_after_seconds`) | `120` | Age after which an in-progress outbox routing claim can be recovered. |
| `INTEGRATION_EVENT_ROUTING_MAX_ATTEMPTS` (`integration_event_routing_max_attempts`) | `10` | Maximum routing attempts before an integration event moves to dead-letter state. |
| `INTEGRATION_EVENT_ROUTING_BACKOFF_SECONDS` (`integration_event_routing_backoff_seconds`) | `10` | Initial exponential delay after integration event routing fails. |
| `INTEGRATION_DELIVERY_RECOVERY_BATCH_SIZE` (`integration_delivery_recovery_batch_size`) | `200` | Maximum generic deliveries recovered per sweep. |
| `INTEGRATION_DELIVERY_RETRY_MAX_ATTEMPTS` (`integration_delivery_retry_max_attempts`) | `5` | Maximum attempts for generic connector deliveries. |
| `INTEGRATION_DELIVERY_RETRY_BACKOFF_SECONDS` (`integration_delivery_retry_backoff_seconds`) | `30` | Initial exponential retry delay. |
| `INTEGRATION_DELIVERY_RETRY_MAX_BACKOFF_SECONDS` (`integration_delivery_retry_max_backoff_seconds`) | `3600` | Retry delay ceiling. |
| `INTEGRATION_DELIVERY_CONCURRENCY_DEFER_SECONDS` (`integration_delivery_concurrency_defer_seconds`) | `5` | Delay before retrying a delivery deferred by an instance concurrency limit. |
| `INTEGRATION_DELIVERY_CIRCUIT_FAILURE_THRESHOLD` (`integration_delivery_circuit_failure_threshold`) | `5` | Consecutive retryable failures before opening an instance circuit. |
| `INTEGRATION_DELIVERY_CIRCUIT_OPEN_SECONDS` (`integration_delivery_circuit_open_seconds`) | `300` | Open-circuit cooldown before a half-open probe. |
| `INTEGRATION_DELIVERY_METRICS_DELAY_SECONDS` (`integration_delivery_metrics_delay_seconds`) | `60` | Minimum terminal-delivery age before metrics aggregation can consume it. |
| `INTEGRATION_DELIVERY_MAINTENANCE_BATCH_SIZE` (`integration_delivery_maintenance_batch_size`) | `1000` | Maximum delivery or event records processed per maintenance batch. |
| `INTEGRATION_DELIVERY_RETENTION_DAYS` (`integration_delivery_retention_days`) | `90` | Terminal generic and linked legacy webhook history retention after metric rollup. |
| `INTEGRATION_EVENT_RETENTION_DAYS` (`integration_event_retention_days`) | `30` | Routed/dead outbox event retention after all deliveries are removed. |
| `INTEGRATION_METRICS_RETENTION_DAYS` (`integration_metrics_retention_days`) | `730` | Hourly delivery rollup retention. |
| `AUDIT_LOG_RETENTION_DAYS` (`audit_log_retention_days`) | `730` | Audit log retention before maintenance removes expired records. |
| `AI_TASK_HISTORY_RETENTION_DAYS` (`ai_task_history_retention_days`) | `180` | Terminal AI task and task-event history retention. |
| `AI_USAGE_RETENTION_DAYS` (`ai_usage_retention_days`) | `730` | AI usage aggregate retention. |
| `TAG_FEEDBACK_RETENTION_DAYS` (`tag_feedback_retention_days`) | `730` | User tag-feedback retention for quality analysis. |
| `INTEGRATION_RUN_RETENTION_DAYS` (`integration_run_retention_days`) | `180` | Terminal integration test and execution run retention. |
| `EXPORT_MAX_ITEMS` (`export_max_items`) | `10000` | Maximum articles in a non-PDF article export. |
| `EXPORT_PDF_MAX_ITEMS` (`export_pdf_max_items`) | `500` | Maximum articles in a readable PDF bundle. Must not exceed `EXPORT_MAX_ITEMS`. |
| `EXPORT_PREVIEW_LIMIT` (`export_preview_limit`) | `25` | Maximum representative rows returned by article export preview. Must not exceed `EXPORT_MAX_ITEMS`. |
| `EXPORT_MAX_UNCOMPRESSED_BYTES` (`export_max_uncompressed_bytes`) | `250000000` | Maximum generated bytes accounted before compression and maximum final artifact size. |
| `EXPORT_LOCK_TTL_SECONDS` (`export_lock_ttl_seconds`) | `900` | Redis-backed per-user export lock lifetime and abandoned-lock recovery interval. Active exports renew the lock every third of this interval. |

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
  - `ghcr.io/patriksi/threatlens-backend:${THREATLENS_IMAGE_TAG:-latest}` for `api`, `worker`, `worker-ai`, `worker-maintenance`, `worker-notifications`, and `beat`
  - `ghcr.io/patriksi/threatlens-web:${THREATLENS_IMAGE_TAG:-latest}` for `web`
- The default compose file pulls fresh ThreatLens application images during `docker compose up`. Source builds require the explicit override: `docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build`.
- `THREATLENS_IMAGE_TAG` defaults to `latest`, which tracks the newest default published image. Set it to an immutable release tag such as `1.0.0` or `v1.0.0`, or to a `sha-<commit>` tag, when you need a pinned deployment.
- `POSTGRES_PASSWORD`, `REDIS_PASSWORD`, `DATABASE_URL`, and `REDIS_URL` are required by compose interpolation unless the generated YAML mapping is pasted into the compose file, so missing values fail the stack instead of silently falling back to weak defaults.
- `docker-compose.yml` runs migrations on API startup by default and can seed the admin account from the API container when `SEED_ADMIN_ON_STARTUP=true`.
- On first boot, either set `SEED_ADMIN_ON_STARTUP=true` for the API service or run `docker compose exec api python -m app.scripts.seed_admin` after migrations, then keep `SEED_ADMIN_ON_STARTUP=false` and `SEED_ADMIN_RESET_PASSWORD_ON_STARTUP=false` for steady state.
- All workers and `beat` depend on healthy `api`, plus healthy DB/Redis, so they start only after schema startup work completes.
- `beat` runs as a dedicated scheduler service so periodic jobs do not multiply with worker replicas.
- `worker` consumes `default`, `ingest`, and `processing`; `worker-ai` consumes only `ai`; `worker-maintenance` consumes only `maintenance`; `worker-notifications` consumes only `notifications`.
- Compose worker concurrency defaults to `4`, `1`, `1`, and `4` respectively. Override these with `WORKER_CONCURRENCY`, `AI_WORKER_CONCURRENCY`, `MAINTENANCE_WORKER_CONCURRENCY`, and `NOTIFICATION_WORKER_CONCURRENCY`. Keep AI concurrency at `1` for a memory-constrained local provider unless provider capacity has been measured.
- The API is not published on a host port by default; use the web service at `http://localhost:3000/api/v1/*` or place the stack behind your own reverse proxy.
- The published OpenAPI schema is exposed through the web proxy at `http://localhost:3000/api/openapi.json`.
- The same compose injects secure defaults for `APP_ENV`, `AUTH_COOKIE_SECURE`, `AUTH_REQUIRE_CSRF`, and `REQUIRE_EXPLICIT_DATA_ENCRYPTION_KEY=true`. It intentionally lets Docker allocate project-scoped networks so multiple stacks do not collide. Set `TRUSTED_PROXY_CIDRS` only when you need the API to trust `X-Forwarded-For` from exact reverse-proxy hops you control.
- `docker-compose.build.yml` forwards exported `APP_VERSION`, `BUILD_DATE`, and `VCS_REF` values into every locally built ThreatLens image as OCI label args. Export them before running the source-build override if you want local image metadata to capture the app version, checked-out revision, and build time; otherwise `APP_VERSION` falls back to the checked-in compose default and the provenance labels fall back to `unknown`.
- `WEB_VITE_API_BASE_URL` from `.env` is passed to the web image as `VITE_API_BASE_URL` and defaults to `/api/v1`. For non-proxied deployments, set it to a full versioned API origin such as `https://api.example.com/v1`.

## Diagnostic Logging

The default is compact human-readable output. For a temporary high-detail troubleshooting session, set:

```dotenv
LOG_LEVEL=DEBUG
LOG_DETAIL=verbose
LOG_FORMAT=text
```

Use `LOG_FORMAT=json` when shipping logs to Loki, ELK, Splunk, or another structured collector. Request and task identifiers are emitted as first-class fields in JSON and as `key=value` context in text logs. Verbose mode records API request starts and Celery task starts/completions, including duration, state, queue, argument count, and keyword names without recording argument values.

For focused subsystem diagnostics, keep the global threshold at `INFO` and override only selected loggers:

```dotenv
LOG_LEVEL=INFO
LOG_DETAIL=verbose
LOG_LEVEL_OVERRIDES=app.services.oidc_client=DEBUG,app.tasks.integration_tasks=DEBUG
```

Verbose mode does not log request or response bodies, cookies, authorization or CSRF headers, passwords, API keys, SMTP credentials, OIDC tokens, or client secrets. Common credential patterns in exception messages are redacted, SQL parameter values remain hidden, and oversized events are truncated. `LOG_INCLUDE_CLIENT_IP` and `LOG_SQL` are separate opt-ins because they have privacy and volume implications.

Apply logging changes by recreating the backend processes:

```bash
docker compose up -d --force-recreate api worker worker-ai worker-maintenance worker-notifications beat
docker compose logs -f api worker worker-ai worker-maintenance worker-notifications beat
```

## Frontend Runtime Values (`web/src/api/client.ts`)

| Key | Value | Purpose |
|---|---|---|
| `DEFAULT_API_BASE_URL` | Dev: `http(s)://<host>:8000/v1`; Prod: `/api/v1` | API base URL fallback. |
| `API_BASE_URL` | `VITE_API_BASE_URL` or fallback | Effective API base URL. |
| `REQUEST_TIMEOUT_MS` | `VITE_API_TIMEOUT_MS` or `15000` | Fetch timeout in milliseconds. |
| `CSRF_COOKIE_NAME` | `VITE_CSRF_COOKIE_NAME` or `threatlens_csrf` | CSRF cookie key used by frontend fetch wrapper. |
| `CSRF_HEADER_NAME` | `VITE_CSRF_HEADER_NAME` or `x-csrf-token` | CSRF header sent on mutating requests. |

## Trust and Egress Notes

- Feed and article fetches, AI provider calls, notification webhooks, and OIDC provider calls are separate outbound trust boundaries with separate deny-by-default private-network controls (`ALLOW_PRIVATE_NETWORK_FETCH`, `ALLOW_PRIVATE_NETWORK_AI`, `ALLOW_PRIVATE_NETWORK_WEBHOOKS`, `ALLOW_PRIVATE_NETWORK_OIDC`).
- OIDC requires HTTPS by default. `ALLOW_INSECURE_HTTP_OIDC=true` is a separate development-only transport opt-in and does not grant access to private hosts. For backward compatibility, `ALLOW_PRIVATE_NETWORK_OIDC=true` continues to permit HTTP only when the target is private; setting both flags makes the two risks explicit. When the ThreatLens callback itself uses HTTP, `AUTH_COOKIE_SECURE=false` is also required and production mode remains intentionally unsuitable for that deployment.
- Notification webhook targets are validated on create, update, test, retry, and delivery. Public webhook targets must use `https`; private-network or internal-only webhook targets require `ALLOW_PRIVATE_NETWORK_WEBHOOKS=true`.
- `TRUSTED_PROXY_CIDRS` only controls whether ThreatLens trusts proxy-supplied client IP headers. It does not widen outbound safety checks, and every trusted proxy hop that can append `X-Forwarded-For` should be included.
- `APP_DATA_ENCRYPTION_KEY` protects feed URLs, stored webhook templates, saved delivery snapshots, and the OIDC client secret at rest; keep it distinct from `JWT_SECRET` and back it up with any `APP_DATA_ENCRYPTION_PREVIOUS_KEYS`.
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
- `dispatch-daily-digest-notifications`: every `300.0` seconds as an idempotent AI Daily Brief notification reconciler
- `dispatch-pending-integration-events`: every `10.0` seconds
- `dispatch-pending-integration-deliveries`: every `10.0` seconds
- `maintain-integration-delivery-history`: every `3600.0` seconds
- `dispatch-daily-ai-brief-generation`: every UTC minute boundary; the task checks the configured UTC hour and minute
- `dispatch-due-report-schedules`: every UTC minute; due times and report windows are evaluated in each schedule's IANA time zone
- `record-beat-heartbeat`: every `BEAT_HEARTBEAT_INTERVAL_SECONDS`

The Beat container runs the scheduler under a watchdog. After the startup grace period, a missing, malformed, future-dated, or stale direct scheduler heartbeat causes the watchdog to stop Beat and exit non-zero so the Compose restart policy can recover it. `/health/beat` also checks the queued Beat-to-worker heartbeat separately, allowing operators to distinguish a stalled scheduler from a delayed maintenance worker.
