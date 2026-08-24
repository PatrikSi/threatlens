import hashlib
import secrets
from functools import lru_cache
from typing import Annotated
from urllib.parse import quote, urlsplit, urlunsplit

from pydantic import PrivateAttr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_PLACEHOLDER_SECRET_PREFIXES = ("replace-with", "change-me", "changeme", "placeholder", "example-", "your-")
_DEFAULT_DATABASE_URL = "postgresql+psycopg://postgres:postgres@db:5432/threatlens"
_DEFAULT_REDIS_URL = "redis://redis:6379/0"


def _looks_like_placeholder_secret(value: str | None) -> bool:
    if value is None:
        return False
    normalized = value.strip().lower()
    if not normalized:
        return False
    if normalized == "admin123":
        return True
    return any(normalized.startswith(prefix) for prefix in _PLACEHOLDER_SECRET_PREFIXES)


def _looks_like_default_service_password(value: str | None) -> bool:
    if value is None:
        return True
    normalized = value.strip().lower()
    if not normalized:
        return True
    return normalized in {"postgres", "redis", "password"} or _looks_like_placeholder_secret(normalized)


def _url_password(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return urlsplit(value).password
    except ValueError:
        return None


def _database_url_uses_weak_default(value: str | None) -> bool:
    if not value:
        return True
    normalized = value.strip()
    if normalized == _DEFAULT_DATABASE_URL:
        return True
    try:
        parts = urlsplit(normalized)
    except ValueError:
        return True
    return (parts.username or "").lower() == "postgres" and (parts.password or "") == "postgres"


def _redis_url_is_passwordless_or_default(value: str | None) -> bool:
    if not value:
        return True
    normalized = value.strip()
    if normalized == _DEFAULT_REDIS_URL:
        return True
    return _looks_like_default_service_password(_url_password(normalized))


def _redis_url_with_password(value: str | None, password: str | None) -> str | None:
    if not value or not password:
        return value
    if _url_password(value):
        return value

    normalized = value.strip()
    if not normalized:
        return value
    try:
        parts = urlsplit(normalized)
        port = parts.port
    except ValueError:
        return value
    if parts.scheme.lower() not in {"redis", "rediss"} or not parts.hostname:
        return value

    hostname = parts.hostname
    host = f"[{hostname}]" if ":" in hostname and not hostname.startswith("[") else hostname
    if port is not None:
        host = f"{host}:{port}"
    username = f"{quote(parts.username, safe='')}:" if parts.username else ":"
    credentials = f"{username}{quote(password.strip(), safe='')}@"
    return urlunsplit((parts.scheme, f"{credentials}{host}", parts.path, parts.query, parts.fragment))


def _is_unsafe_credentialed_cors_origin(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"*", "null"} or "*" in normalized:
        return True
    try:
        parts = urlsplit(normalized)
    except ValueError:
        return True
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return True
    if parts.path not in {"", "/"} or parts.query or parts.fragment:
        return True
    return False


def _is_unsafe_allowed_host(value: str) -> bool:
    normalized = value.strip().lower()
    if not normalized or normalized == "*":
        return True
    return "://" in normalized or "/" in normalized


def _generate_runtime_secret() -> str:
    return secrets.token_urlsafe(48)


def _derive_development_secret(*, purpose: str, app_env: str, database_url: str, redis_url: str, admin_email: str) -> str:
    seed = "\x1f".join(
        [
            "threatlens-development-secret",
            purpose,
            app_env.strip().lower(),
            database_url.strip(),
            redis_url.strip(),
            admin_email.strip().lower(),
        ]
    )
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return f"dev-{purpose}-{digest}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    _jwt_secret_was_derived: bool = PrivateAttr(default=False)
    _app_data_encryption_key_was_derived: bool = PrivateAttr(default=False)

    app_env: str = "development"
    database_url: str = _DEFAULT_DATABASE_URL
    redis_url: str = _DEFAULT_REDIS_URL
    postgres_password: str | None = None
    redis_password: str | None = None
    jwt_secret: str = ""
    app_data_encryption_key: str | None = None
    app_data_encryption_previous_keys: Annotated[list[str], NoDecode] = []
    require_explicit_data_encryption_key: bool = False
    jwt_algorithm: str = "HS256"
    jwt_expires_minutes: int = 60 * 24
    allow_legacy_unscoped_tokens: bool = False
    allow_self_registration: bool = False
    default_api_token_expiry_days: int = 90
    ai_enabled: bool = False
    ai_api_key: str | None = None
    public_app_url: str | None = None
    expose_api_docs_in_production: bool = False
    expose_openapi_schema_in_production: bool = True

    auth_cookie_name: str = "threatlens_session"
    auth_cookie_domain: str | None = None
    auth_cookie_path: str = "/"
    auth_cookie_secure: bool = False
    auth_cookie_samesite: str = "lax"
    auth_csrf_cookie_name: str = "threatlens_csrf"
    auth_csrf_header_name: str = "x-csrf-token"
    auth_require_csrf: bool = True
    trusted_proxy_cidrs: Annotated[list[str], NoDecode] = []
    trusted_proxy_hosts: Annotated[list[str], NoDecode] = []
    allowed_hosts: Annotated[list[str], NoDecode] = ["api", "localhost", "127.0.0.1", "::1", "testserver"]

    admin_email: str = "admin@example.com"
    admin_password: str = "admin123"
    seed_admin_force_role: bool = False
    seed_admin_reactivate_existing: bool = False
    seed_admin_on_startup: bool = False
    seed_admin_reset_password_on_startup: bool = False
    run_migrations_on_startup: bool = False

    fetch_user_agent: str = "ThreatLensBot/1.0 (+https://localhost)"
    feed_connect_timeout_seconds: int = 5
    feed_read_timeout_seconds: int = 15
    feed_max_bytes: int = 2_000_000
    article_connect_timeout_seconds: int = 5
    article_read_timeout_seconds: int = 20
    article_max_bytes: int = 4_000_000
    allow_private_network_fetch: bool = False
    allow_private_network_ai: bool = False
    allow_private_network_webhooks: bool = False
    allow_private_network_oidc: bool = False
    allow_insecure_http_oidc: bool = False
    outbound_max_redirects: int = 5
    per_domain_concurrency: int = 2
    auth_login_max_attempts: int = 8
    auth_login_ip_max_attempts: int = 50
    auth_login_window_seconds: int = 300
    auth_login_lockout_seconds: int = 900
    redis_connect_timeout_seconds: float = 2.0
    redis_socket_timeout_seconds: float = 2.0
    database_connect_timeout_seconds: int = 5
    database_statement_timeout_ms: int = 30_000
    database_pool_timeout_seconds: int = 10
    api_token_last_used_update_interval_seconds: int = 300
    oidc_transaction_cookie_name: str = "threatlens_oidc_transaction"
    oidc_transaction_ttl_seconds: int = 600
    oidc_callback_path: str = "/api/v1/auth/oidc/callback"
    oidc_metadata_cache_seconds: int = 300
    oidc_connect_timeout_seconds: float = 5
    oidc_read_timeout_seconds: float = 10
    oidc_max_response_bytes: int = 1_000_000

    probe_feed_metadata_on_create: bool = False
    probe_feed_metadata_on_import: bool = False
    max_metadata_backfill_tasks_per_request: int = 100
    dispatch_due_feeds_batch_size: int = 500
    dispatch_feed_claim_seconds: int = 900
    dispatch_items_missing_articles_batch_size: int = 200
    dispatch_items_missing_articles_after_seconds: int = 300
    dispatch_unclassified_items_batch_size: int = 200
    dispatch_items_missing_iocs_batch_size: int = 200
    dispatch_items_missing_ai_enrichment_batch_size: int = 200
    dispatch_items_failed_ai_enrichment_after_seconds: int = 3600
    ai_auto_enrich_new_item_max_age_hours: int = 24
    ai_daily_brief_source_audit_limit: int = 500
    dispatch_feed_metadata_scan_limit: int = 250
    dispatch_feed_metadata_queue_limit: int = 50
    dispatch_ai_reprocess_batch_size: int = 100
    celery_visibility_timeout_seconds: int = 3600
    report_generation_lease_seconds: int = 600
    report_legacy_worker_grace_seconds: int = 86_400
    report_task_infrastructure_max_retries: int = 5
    report_task_infrastructure_retry_backoff_seconds: int = 30
    report_task_infrastructure_retry_max_backoff_seconds: int = 900
    report_schedule_max_attempts: int = 5
    report_schedule_retry_backoff_seconds: int = 60
    report_schedule_retry_max_backoff_seconds: int = 3600
    report_dispatch_batch_size: int = 100
    report_dispatch_max_attempts: int = 10
    report_dispatch_claim_seconds: int = 60
    report_dispatch_start_grace_seconds: int = 3600
    report_dispatch_retry_backoff_seconds: int = 15
    report_dispatch_retry_max_backoff_seconds: int = 900

    alert_matches_keyword_cap: int = 512
    stats_top_domains_limit: int = 10

    export_max_items: int = 10_000
    export_pdf_max_items: int = 500
    export_preview_limit: int = 25
    export_max_uncompressed_bytes: int = 250_000_000
    export_lock_ttl_seconds: int = 900

    log_level: str = "INFO"
    log_level_overrides: Annotated[list[str], NoDecode] = []
    log_format: str = "text"
    log_detail: str = "standard"
    log_include_client_ip: bool = False
    log_slow_request_ms: int = 1000
    log_max_event_chars: int = 20_000
    log_sql: bool = False
    health_worker_ping_timeout_seconds: float = 1.0
    beat_heartbeat_key: str = "threatlens:beat:heartbeat"
    beat_scheduler_heartbeat_key: str = "threatlens:beat:scheduler-heartbeat"
    beat_heartbeat_ttl_seconds: int = 180
    beat_heartbeat_stale_after_seconds: int = 180
    beat_heartbeat_interval_seconds: int = 60
    beat_watchdog_startup_grace_seconds: int = 240
    beat_watchdog_check_interval_seconds: int = 15
    beat_watchdog_terminate_timeout_seconds: int = 10
    notification_delivery_enqueue_batch_size: int = 100
    notification_delivery_recovery_batch_size: int = 100
    notification_delivery_sending_stale_after_seconds: int = 120
    notification_delivery_queue_degraded_after_seconds: int = 300
    notification_delivery_retry_max_attempts: int = 3
    notification_delivery_retry_backoff_seconds: int = 30
    integration_event_routing_batch_size: int = 200
    integration_event_routing_stale_after_seconds: int = 120
    integration_event_routing_max_attempts: int = 10
    integration_event_routing_backoff_seconds: int = 10
    integration_delivery_recovery_batch_size: int = 200
    integration_delivery_retry_max_attempts: int = 5
    integration_delivery_retry_backoff_seconds: int = 30
    integration_delivery_retry_max_backoff_seconds: int = 3600
    integration_delivery_concurrency_defer_seconds: int = 5
    integration_delivery_circuit_failure_threshold: int = 5
    integration_delivery_circuit_open_seconds: int = 300
    integration_delivery_metrics_delay_seconds: int = 60
    integration_delivery_maintenance_batch_size: int = 1000
    integration_delivery_retention_days: int = 90
    integration_event_retention_days: int = 30
    integration_metrics_retention_days: int = 730
    audit_log_retention_days: int = 730
    ai_task_history_retention_days: int = 180
    ai_usage_retention_days: int = 730
    tag_feedback_retention_days: int = 730
    integration_run_retention_days: int = 180
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:3000", "http://127.0.0.1:3000"]

    @field_validator(
        "cors_origins",
        "trusted_proxy_cidrs",
        "trusted_proxy_hosts",
        "allowed_hosts",
        "app_data_encryption_previous_keys",
        "log_level_overrides",
        mode="before",
    )
    @classmethod
    def _parse_csv_list(cls, value):
        if isinstance(value, str):
            return [entry.strip() for entry in value.split(",") if entry.strip()]
        return value

    @field_validator("auth_cookie_samesite", mode="before")
    @classmethod
    def _normalize_samesite(cls, value):
        normalized = str(value).strip().lower()
        if normalized not in {"lax", "strict", "none"}:
            raise ValueError("auth_cookie_samesite must be one of: lax, strict, none")
        return normalized

    @field_validator("auth_csrf_header_name", mode="before")
    @classmethod
    def _normalize_header_name(cls, value):
        return str(value).strip().lower()

    @field_validator("oidc_callback_path", mode="before")
    @classmethod
    def _normalize_oidc_callback_path(cls, value):
        normalized = str(value).strip()
        if not normalized.startswith("/") or normalized.startswith("//") or "?" in normalized or "#" in normalized:
            raise ValueError("oidc_callback_path must be an absolute URL path without a query or fragment")
        return normalized

    @field_validator("public_app_url", mode="before")
    @classmethod
    def _normalize_public_app_url(cls, value):
        normalized = str(value or "").strip().rstrip("/")
        if not normalized:
            return None
        try:
            parts = urlsplit(normalized)
        except ValueError as exc:
            raise ValueError("public_app_url must be a valid HTTP(S) URL") from exc
        if (
            parts.scheme not in {"http", "https"}
            or not parts.netloc
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
        ):
            raise ValueError(
                "public_app_url must be an HTTP(S) URL without credentials, query, or fragment"
            )
        return normalized

    @field_validator(
        "oidc_transaction_ttl_seconds",
        "oidc_metadata_cache_seconds",
        "oidc_max_response_bytes",
    )
    @classmethod
    def _validate_positive_oidc_limits(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("OIDC transaction, cache, and response limits must be greater than zero")
        return value

    @field_validator("oidc_connect_timeout_seconds", "oidc_read_timeout_seconds")
    @classmethod
    def _validate_positive_oidc_timeouts(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("OIDC timeout values must be greater than zero")
        return value

    @field_validator("redis_connect_timeout_seconds", "redis_socket_timeout_seconds")
    @classmethod
    def _validate_positive_redis_timeouts(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("Redis timeout values must be greater than zero")
        return value

    @field_validator(
        "auth_login_max_attempts",
        "auth_login_ip_max_attempts",
        "database_connect_timeout_seconds",
        "database_statement_timeout_ms",
        "database_pool_timeout_seconds",
        "celery_visibility_timeout_seconds",
        "report_generation_lease_seconds",
        "report_legacy_worker_grace_seconds",
        "report_task_infrastructure_max_retries",
        "report_task_infrastructure_retry_backoff_seconds",
        "report_task_infrastructure_retry_max_backoff_seconds",
        "report_schedule_max_attempts",
        "report_schedule_retry_backoff_seconds",
        "report_schedule_retry_max_backoff_seconds",
        "report_dispatch_batch_size",
        "report_dispatch_max_attempts",
        "report_dispatch_claim_seconds",
        "report_dispatch_start_grace_seconds",
        "report_dispatch_retry_backoff_seconds",
        "report_dispatch_retry_max_backoff_seconds",
    )
    @classmethod
    def _validate_positive_operational_limits(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("Authentication and database limits must be greater than zero")
        return value

    @field_validator("report_generation_lease_seconds")
    @classmethod
    def _validate_report_generation_lease(cls, value: int) -> int:
        if value < 360:
            raise ValueError(
                "report_generation_lease_seconds must cover the maximum AI provider request timeout"
            )
        return value

    @model_validator(mode="after")
    def _validate_report_visibility_timeout(self):
        if (
            self.celery_visibility_timeout_seconds
            <= self.report_generation_lease_seconds
        ):
            raise ValueError(
                "celery_visibility_timeout_seconds must be greater than "
                "report_generation_lease_seconds"
            )
        return self

    @model_validator(mode="after")
    def _validate_report_legacy_worker_grace(self):
        if self.report_legacy_worker_grace_seconds < self.celery_visibility_timeout_seconds:
            raise ValueError(
                "report_legacy_worker_grace_seconds must be at least "
                "celery_visibility_timeout_seconds"
            )
        return self

    @model_validator(mode="after")
    def _validate_report_task_infrastructure_retry_limits(self):
        if (
            self.report_task_infrastructure_retry_max_backoff_seconds
            < self.report_task_infrastructure_retry_backoff_seconds
        ):
            raise ValueError(
                "report_task_infrastructure_retry_max_backoff_seconds must be at "
                "least report_task_infrastructure_retry_backoff_seconds"
            )
        return self

    @model_validator(mode="after")
    def _validate_report_schedule_retry_limits(self):
        if (
            self.report_schedule_retry_max_backoff_seconds
            < self.report_schedule_retry_backoff_seconds
        ):
            raise ValueError(
                "report_schedule_retry_max_backoff_seconds must be at least "
                "report_schedule_retry_backoff_seconds"
            )
        return self

    @model_validator(mode="after")
    def _validate_report_dispatch_retry_limits(self):
        if (
            self.report_dispatch_retry_max_backoff_seconds
            < self.report_dispatch_retry_backoff_seconds
        ):
            raise ValueError(
                "report_dispatch_retry_max_backoff_seconds must be at least "
                "report_dispatch_retry_backoff_seconds"
            )
        return self

    @field_validator(
        "export_max_items",
        "export_pdf_max_items",
        "export_preview_limit",
        "export_max_uncompressed_bytes",
        "export_lock_ttl_seconds",
    )
    @classmethod
    def _validate_positive_export_limits(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("Export limits must be greater than zero")
        return value

    @model_validator(mode="after")
    def _validate_export_limits(self):
        if self.export_pdf_max_items > self.export_max_items:
            raise ValueError("export_pdf_max_items must not exceed export_max_items")
        if self.export_preview_limit > self.export_max_items:
            raise ValueError("export_preview_limit must not exceed export_max_items")
        return self

    @model_validator(mode="after")
    def _validate_login_ip_threshold(self):
        if self.auth_login_ip_max_attempts < self.auth_login_max_attempts:
            raise ValueError("auth_login_ip_max_attempts must be at least auth_login_max_attempts")
        return self

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, value):
        normalized = str(value).strip().upper() or "INFO"
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("log_level must be one of: DEBUG, INFO, WARNING, ERROR, CRITICAL")
        return normalized

    @field_validator("log_level_overrides")
    @classmethod
    def _validate_log_level_overrides(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            logger_name, separator, level = value.partition("=")
            logger_name = logger_name.strip()
            level = level.strip().upper()
            if not separator or not logger_name or any(character.isspace() for character in logger_name):
                raise ValueError("log_level_overrides entries must use logger.name=LEVEL")
            if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
                raise ValueError("log_level_overrides levels must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")
            normalized.append(f"{logger_name}={level}")
        return normalized

    @field_validator("log_format", mode="before")
    @classmethod
    def _normalize_log_format(cls, value):
        normalized = str(value).strip().lower() or "text"
        if normalized not in {"text", "json"}:
            raise ValueError("log_format must be one of: text, json")
        return normalized

    @field_validator("log_detail", mode="before")
    @classmethod
    def _normalize_log_detail(cls, value):
        normalized = str(value).strip().lower() or "standard"
        if normalized not in {"standard", "verbose"}:
            raise ValueError("log_detail must be one of: standard, verbose")
        return normalized

    @field_validator("log_slow_request_ms", "log_max_event_chars")
    @classmethod
    def _validate_positive_logging_limits(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("Logging limits must be greater than zero")
        return value

    @field_validator(
        "beat_heartbeat_ttl_seconds",
        "beat_heartbeat_stale_after_seconds",
        "beat_heartbeat_interval_seconds",
        "beat_watchdog_check_interval_seconds",
        "beat_watchdog_terminate_timeout_seconds",
    )
    @classmethod
    def _validate_positive_beat_timing(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("Beat heartbeat and watchdog timing values must be greater than zero")
        return value

    @field_validator("beat_watchdog_startup_grace_seconds")
    @classmethod
    def _validate_beat_startup_grace(cls, value: int) -> int:
        if value < 0:
            raise ValueError("beat_watchdog_startup_grace_seconds must not be negative")
        return value

    @field_validator("app_data_encryption_key", mode="before")
    @classmethod
    def _normalize_optional_secret(cls, value):
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @model_validator(mode="after")
    def _validate_beat_timing(self):
        if self.beat_watchdog_startup_grace_seconds < self.beat_heartbeat_interval_seconds:
            raise ValueError("beat_watchdog_startup_grace_seconds must cover at least one heartbeat interval")
        return self

    @model_validator(mode="after")
    def _validate_production_security(self):
        is_production = self.app_env.lower() in {"production", "prod"}
        self._jwt_secret_was_derived = False
        self._app_data_encryption_key_was_derived = False

        if self.redis_password:
            normalized_redis_password = self.redis_password.strip()
            self.redis_password = normalized_redis_password or None
            self.redis_url = _redis_url_with_password(self.redis_url, self.redis_password) or self.redis_url

        if not self.jwt_secret or _looks_like_placeholder_secret(self.jwt_secret) or len(self.jwt_secret) < 32:
            if is_production:
                raise ValueError("jwt_secret must be explicitly set in production")
            self._jwt_secret_was_derived = True
            self.jwt_secret = _derive_development_secret(
                purpose="jwt",
                app_env=self.app_env,
                database_url=self.database_url,
                redis_url=self.redis_url,
                admin_email=self.admin_email,
            )

        if (
            not self.app_data_encryption_key
            or _looks_like_placeholder_secret(self.app_data_encryption_key)
            or len(self.app_data_encryption_key) < 32
        ):
            if is_production or self.require_explicit_data_encryption_key:
                raise ValueError("app_data_encryption_key must be explicitly set")
            self._app_data_encryption_key_was_derived = True
            self.app_data_encryption_key = _derive_development_secret(
                purpose="app-data",
                app_env=self.app_env,
                database_url=self.database_url,
                redis_url=self.redis_url,
                admin_email=self.admin_email,
            )

        if is_production:
            if _looks_like_placeholder_secret(self.jwt_secret) or len(self.jwt_secret) < 32:
                raise ValueError("jwt_secret must be set and at least 32 characters in production")
            if (
                not self.app_data_encryption_key
                or _looks_like_placeholder_secret(self.app_data_encryption_key)
                or len(self.app_data_encryption_key) < 32
            ):
                raise ValueError("app_data_encryption_key must be set and at least 32 characters in production")
            if _looks_like_placeholder_secret(self.admin_password):
                raise ValueError("admin_password must not use a default or placeholder value in production")
            if not self.auth_cookie_secure:
                raise ValueError("auth_cookie_secure must be true in production")
            if not self.auth_require_csrf:
                raise ValueError("auth_require_csrf must be true in production")
            if self.allow_legacy_unscoped_tokens:
                raise ValueError("allow_legacy_unscoped_tokens is not allowed in production")
            if any(_is_unsafe_credentialed_cors_origin(origin) for origin in self.cors_origins):
                raise ValueError("cors_origins must be explicit http(s) origins when credentialed CORS is enabled in production")
            if not self.allowed_hosts or any(_is_unsafe_allowed_host(host) for host in self.allowed_hosts):
                raise ValueError("allowed_hosts must list explicit trusted hosts in production")
            if _database_url_uses_weak_default(self.database_url):
                raise ValueError("database_url must use explicit non-default database credentials in production")
            if _looks_like_default_service_password(self.postgres_password):
                raise ValueError("postgres_password must be explicitly set to a non-default value in production")
            if _redis_url_is_passwordless_or_default(self.redis_url):
                raise ValueError("redis_url must include a non-default password in production")
            if _looks_like_default_service_password(self.redis_password):
                raise ValueError("redis_password must be explicitly set to a non-default value in production")
        if self.seed_admin_on_startup and _looks_like_placeholder_secret(self.admin_password):
            raise ValueError("admin_password must not use a default or placeholder value when seed_admin_on_startup is enabled")
        return self

    @property
    def jwt_secret_was_derived(self) -> bool:
        return self._jwt_secret_was_derived

    @property
    def app_data_encryption_key_was_derived(self) -> bool:
        return self._app_data_encryption_key_was_derived


@lru_cache
def get_settings() -> Settings:
    return Settings()
