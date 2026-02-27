from functools import lru_cache

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    database_url: str = "postgresql+psycopg://postgres:postgres@db:5432/threatlens"
    redis_url: str = "redis://redis:6379/0"
    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"
    jwt_expires_minutes: int = 60 * 24
    allow_legacy_unscoped_tokens: bool = True
    allow_self_registration: bool = False
    default_api_token_expiry_days: int = 90

    admin_email: str = "admin@example.com"
    admin_password: str = "admin123"

    fetch_user_agent: str = "ThreatLensBot/1.0 (+https://localhost)"
    feed_connect_timeout_seconds: int = 5
    feed_read_timeout_seconds: int = 15
    feed_max_bytes: int = 2_000_000
    article_connect_timeout_seconds: int = 5
    article_read_timeout_seconds: int = 20
    article_max_bytes: int = 4_000_000
    outbound_max_redirects: int = 5
    per_domain_concurrency: int = 2
    allow_private_network_fetch: bool = False
    auth_login_max_attempts: int = 8
    auth_login_window_seconds: int = 300
    auth_login_lockout_seconds: int = 900
    api_token_last_used_update_interval_seconds: int = 300
    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, value):
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @model_validator(mode="after")
    def _validate_production_security(self):
        if self.app_env.lower() in {"production", "prod"}:
            if self.jwt_secret == "change-me" or len(self.jwt_secret) < 32:
                raise ValueError("jwt_secret must be set and at least 32 characters in production")
            if self.admin_password == "admin123":
                raise ValueError("admin_password default is not allowed in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
