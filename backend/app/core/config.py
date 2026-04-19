from functools import lru_cache
from typing import Annotated

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_PLACEHOLDER_SECRET_PREFIXES = ("replace-with", "change-me", "changeme", "placeholder", "example-", "your-")


def _looks_like_placeholder_secret(value: str | None) -> bool:
    if value is None:
        return False
    normalized = value.strip().lower()
    if not normalized:
        return False
    if normalized == "admin123":
        return True
    return any(normalized.startswith(prefix) for prefix in _PLACEHOLDER_SECRET_PREFIXES)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    database_url: str = "postgresql+psycopg://postgres:postgres@db:5432/threatlens"
    redis_url: str = "redis://redis:6379/0"
    jwt_secret: str = "change-me"
    app_data_encryption_key: str | None = None
    app_data_encryption_previous_keys: Annotated[list[str], NoDecode] = []
    jwt_algorithm: str = "HS256"
    jwt_expires_minutes: int = 60 * 24
    allow_legacy_unscoped_tokens: bool = False
    allow_self_registration: bool = False
    default_api_token_expiry_days: int = 90
    ai_enabled: bool = False
    ai_api_key: str | None = None
    expose_api_docs_in_production: bool = False

    auth_cookie_name: str = "threatlens_session"
    auth_cookie_domain: str | None = None
    auth_cookie_path: str = "/"
    auth_cookie_secure: bool = False
    auth_cookie_samesite: str = "lax"
    auth_csrf_cookie_name: str = "threatlens_csrf"
    auth_csrf_header_name: str = "x-csrf-token"
    auth_require_csrf: bool = True
    trusted_proxy_cidrs: Annotated[list[str], NoDecode] = []

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
    outbound_max_redirects: int = 5
    per_domain_concurrency: int = 2
    auth_login_max_attempts: int = 8
    auth_login_window_seconds: int = 300
    auth_login_lockout_seconds: int = 900
    api_token_last_used_update_interval_seconds: int = 300

    probe_feed_metadata_on_create: bool = False
    probe_feed_metadata_on_import: bool = False
    max_metadata_backfill_tasks_per_request: int = 100
    dispatch_due_feeds_batch_size: int = 500
    dispatch_feed_claim_seconds: int = 900
    dispatch_items_missing_articles_batch_size: int = 200
    dispatch_items_missing_articles_after_seconds: int = 300
    dispatch_unclassified_items_batch_size: int = 200
    dispatch_items_missing_iocs_batch_size: int = 200
    dispatch_feed_metadata_scan_limit: int = 250
    dispatch_feed_metadata_queue_limit: int = 50
    dispatch_ai_reprocess_batch_size: int = 100

    alert_matches_keyword_cap: int = 512
    stats_top_domains_limit: int = 10

    log_level: str = "INFO"
    health_worker_ping_timeout_seconds: float = 1.0
    beat_heartbeat_key: str = "threatlens:beat:heartbeat"
    beat_heartbeat_ttl_seconds: int = 180
    beat_heartbeat_stale_after_seconds: int = 180
    beat_heartbeat_interval_seconds: int = 60
    notification_delivery_enqueue_batch_size: int = 100
    notification_delivery_recovery_batch_size: int = 100
    notification_delivery_sending_stale_after_seconds: int = 120
    notification_delivery_queue_degraded_after_seconds: int = 300
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:3000", "http://127.0.0.1:3000"]

    @field_validator("cors_origins", "trusted_proxy_cidrs", "app_data_encryption_previous_keys", mode="before")
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

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, value):
        return str(value).strip().upper() or "INFO"

    @field_validator("app_data_encryption_key", mode="before")
    @classmethod
    def _normalize_optional_secret(cls, value):
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @model_validator(mode="after")
    def _validate_production_security(self):
        if self.app_env.lower() in {"production", "prod"}:
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
        if self.seed_admin_on_startup and _looks_like_placeholder_secret(self.admin_password):
            raise ValueError("admin_password must not use a default or placeholder value when seed_admin_on_startup is enabled")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
