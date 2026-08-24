import pytest

from app.core.config import Settings


def isolated_settings(**kwargs) -> Settings:
    return Settings(_env_file=None, **kwargs)


def production_settings_kwargs(**overrides):
    kwargs = {
        "app_env": "production",
        "database_url": "postgresql+psycopg://threatlens:strong-db-pass@db:5432/threatlens",
        "redis_url": "redis://:strong-redis-pass@redis:6379/0",
        "postgres_password": "strong-db-pass",
        "redis_password": "strong-redis-pass",
        "jwt_secret": "x" * 48,
        "app_data_encryption_key": "y" * 48,
        "admin_password": "StrongPass123!",
        "auth_cookie_secure": True,
        "auth_require_csrf": True,
    }
    kwargs.update(overrides)
    return kwargs


def test_cors_origins_parses_csv():
    settings = isolated_settings(cors_origins="http://localhost:3000, https://threatlens.local")
    assert settings.cors_origins == ["http://localhost:3000", "https://threatlens.local"]


def test_cors_origins_parses_csv_from_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000, https://threatlens.local")
    settings = Settings(_env_file=None)
    assert settings.cors_origins == ["http://localhost:3000", "https://threatlens.local"]


def test_trusted_proxy_cidrs_parses_csv_from_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TRUSTED_PROXY_CIDRS", "127.0.0.1/32,::1/128,172.16.0.0/12")
    settings = Settings(_env_file=None)
    assert settings.trusted_proxy_cidrs == ["127.0.0.1/32", "::1/128", "172.16.0.0/12"]


def test_trusted_proxy_hosts_parses_csv_from_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TRUSTED_PROXY_HOSTS", "web, edge-proxy")
    settings = Settings(_env_file=None)
    assert settings.trusted_proxy_hosts == ["web", "edge-proxy"]


def test_ip_login_threshold_cannot_be_lower_than_account_threshold():
    with pytest.raises(ValueError, match="auth_login_ip_max_attempts"):
        isolated_settings(auth_login_max_attempts=10, auth_login_ip_max_attempts=9)


def test_report_generation_lease_covers_provider_timeout():
    with pytest.raises(ValueError, match="report_generation_lease_seconds"):
        isolated_settings(report_generation_lease_seconds=300)


@pytest.mark.parametrize("visibility_timeout", [599, 600])
def test_celery_visibility_timeout_exceeds_report_lease(
    visibility_timeout: int,
):
    with pytest.raises(
        ValueError,
        match=(
            "celery_visibility_timeout_seconds must be greater than "
            "report_generation_lease_seconds"
        ),
    ):
        isolated_settings(
            celery_visibility_timeout_seconds=visibility_timeout,
            report_generation_lease_seconds=600,
        )


def test_legacy_report_worker_grace_covers_visibility_timeout():
    with pytest.raises(
        ValueError,
        match="report_legacy_worker_grace_seconds",
    ):
        isolated_settings(
            celery_visibility_timeout_seconds=3600,
            report_legacy_worker_grace_seconds=3599,
        )


def test_report_schedule_retry_backoff_must_be_bounded():
    with pytest.raises(
        ValueError, match="report_schedule_retry_max_backoff_seconds"
    ):
        isolated_settings(
            report_schedule_retry_backoff_seconds=120,
            report_schedule_retry_max_backoff_seconds=60,
        )


def test_allowed_hosts_parses_csv_from_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ALLOWED_HOSTS", "api, threatlens.example.com")
    settings = Settings(_env_file=None)
    assert settings.allowed_hosts == ["api", "threatlens.example.com"]


def test_production_requires_strong_jwt_secret():
    with pytest.raises(ValueError):
        isolated_settings(**production_settings_kwargs(jwt_secret="change-me"))


def test_production_requires_dedicated_data_encryption_key():
    with pytest.raises(ValueError):
        isolated_settings(
            **production_settings_kwargs(app_data_encryption_key=None),
        )


def test_non_production_can_require_explicit_data_encryption_key():
    with pytest.raises(ValueError, match="app_data_encryption_key must be explicitly set"):
        isolated_settings(
            app_env="development",
            require_explicit_data_encryption_key=True,
            jwt_secret="x" * 48,
        )


def test_production_rejects_default_admin_password():
    with pytest.raises(ValueError):
        isolated_settings(**production_settings_kwargs(admin_password="admin123"))


def test_admin_seeding_rejects_default_admin_password():
    with pytest.raises(ValueError):
        isolated_settings(seed_admin_on_startup=True, admin_password="admin123")


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("jwt_secret", "replace-with-long-random-secret"),
        ("app_data_encryption_key", "replace-with-separate-long-random-secret"),
        ("admin_password", "replace-with-strong-admin-password"),
    ],
)
def test_production_rejects_placeholder_secret_values(field_name: str, field_value: str):
    kwargs = production_settings_kwargs()
    kwargs[field_name] = field_value
    with pytest.raises(ValueError):
        isolated_settings(**kwargs)


def test_production_requires_secure_auth_cookie():
    with pytest.raises(ValueError):
        isolated_settings(**production_settings_kwargs(auth_cookie_secure=False))


def test_production_requires_csrf_for_cookie_auth():
    with pytest.raises(ValueError):
        isolated_settings(**production_settings_kwargs(auth_require_csrf=False))


def test_production_rejects_legacy_unscoped_token_bypass():
    with pytest.raises(ValueError):
        isolated_settings(**production_settings_kwargs(allow_legacy_unscoped_tokens=True))


@pytest.mark.parametrize("origin", ["*", "null", "https://*.example.com", "https://example.com/path"])
def test_production_rejects_unsafe_credentialed_cors_origins(origin: str):
    with pytest.raises(ValueError, match="cors_origins"):
        isolated_settings(**production_settings_kwargs(cors_origins=[origin]))


@pytest.mark.parametrize("host", ["*", "", "https://example.com", "example.com/path"])
def test_production_rejects_unsafe_allowed_hosts(host: str):
    with pytest.raises(ValueError, match="allowed_hosts"):
        isolated_settings(**production_settings_kwargs(allowed_hosts=[host]))


@pytest.mark.parametrize(
    ("field_name", "field_value", "message"),
    [
        ("database_url", "postgresql+psycopg://postgres:postgres@db:5432/threatlens", "database_url"),
        ("postgres_password", None, "postgres_password"),
        ("postgres_password", "postgres", "postgres_password"),
        ("redis_url", "redis://:redis@redis:6379/0", "redis_url"),
        ("redis_password", None, "redis_password"),
        ("redis_password", "redis", "redis_password"),
    ],
)
def test_production_rejects_weak_database_and_redis_defaults(field_name: str, field_value: str | None, message: str):
    with pytest.raises(ValueError, match=message):
        isolated_settings(**production_settings_kwargs(**{field_name: field_value}))


def test_bootstrap_mutation_flags_default_off():
    settings = isolated_settings()

    assert settings.run_migrations_on_startup is False
    assert settings.seed_admin_on_startup is False


@pytest.mark.parametrize(
    ("field_name", "field_value", "message"),
    [
        ("log_level", "TRACE", "log_level"),
        ("log_format", "xml", "log_format"),
        ("log_detail", "everything", "log_detail"),
        ("log_level_overrides", ["app.services.oidc_client=TRACE"], "log_level_overrides"),
        ("log_slow_request_ms", 0, "greater than zero"),
        ("log_max_event_chars", 0, "greater than zero"),
    ],
)
def test_logging_settings_reject_invalid_values(field_name: str, field_value: object, message: str):
    with pytest.raises(ValueError, match=message):
        isolated_settings(**{field_name: field_value})


def test_logging_settings_normalize_supported_values():
    settings = isolated_settings(
        log_level="debug",
        log_level_overrides=["app.services.oidc_client=debug"],
        log_format="JSON",
        log_detail="VERBOSE",
    )

    assert settings.log_level == "DEBUG"
    assert settings.log_level_overrides == ["app.services.oidc_client=DEBUG"]
    assert settings.log_format == "json"
    assert settings.log_detail == "verbose"


@pytest.mark.parametrize(
    "field_name",
    [
        "export_max_items",
        "export_pdf_max_items",
        "export_preview_limit",
        "export_max_uncompressed_bytes",
        "export_lock_ttl_seconds",
    ],
)
def test_export_limits_must_be_positive(field_name: str):
    with pytest.raises(ValueError, match="greater than zero"):
        isolated_settings(**{field_name: 0})


def test_export_pdf_and_preview_limits_must_fit_item_limit():
    with pytest.raises(ValueError, match="export_pdf_max_items"):
        isolated_settings(export_max_items=10, export_pdf_max_items=11)
    with pytest.raises(ValueError, match="export_preview_limit"):
        isolated_settings(export_max_items=10, export_pdf_max_items=10, export_preview_limit=11)


@pytest.mark.parametrize(
    "field_name",
    [
        "beat_heartbeat_ttl_seconds",
        "beat_heartbeat_stale_after_seconds",
        "beat_heartbeat_interval_seconds",
        "beat_watchdog_check_interval_seconds",
        "beat_watchdog_terminate_timeout_seconds",
    ],
)
def test_beat_timing_settings_must_be_positive(field_name: str):
    with pytest.raises(ValueError, match="greater than zero"):
        isolated_settings(**{field_name: 0})


def test_beat_watchdog_grace_must_cover_one_heartbeat_interval():
    with pytest.raises(ValueError, match="cover at least one heartbeat interval"):
        isolated_settings(
            beat_heartbeat_interval_seconds=60,
            beat_watchdog_startup_grace_seconds=59,
        )


def test_redis_password_is_applied_to_passwordless_redis_url():
    settings = isolated_settings(
        redis_url="redis://redis:6379/0",
        redis_password="strong-redis-pass",
    )

    assert settings.redis_url == "redis://:strong-redis-pass@redis:6379/0"


def test_redis_url_keeps_explicit_password():
    settings = isolated_settings(
        redis_url="redis://:explicit-pass@redis:6379/0",
        redis_password="strong-redis-pass",
    )

    assert settings.redis_url == "redis://:explicit-pass@redis:6379/0"


def test_production_accepts_passwordless_redis_url_when_redis_password_is_set():
    settings = isolated_settings(**production_settings_kwargs(redis_url="redis://redis:6379/0"))

    assert settings.redis_url == "redis://:strong-redis-pass@redis:6379/0"


def test_development_generates_runtime_secrets_when_not_configured():
    settings = isolated_settings()

    assert settings.jwt_secret
    assert settings.app_data_encryption_key
    assert settings.jwt_secret != settings.app_data_encryption_key
    assert settings.jwt_secret != "change-me"
    assert settings.jwt_secret_was_derived is True
    assert settings.app_data_encryption_key_was_derived is True


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("jwt_secret", "change-me"),
        ("app_data_encryption_key", "change-me"),
    ],
)
def test_placeholder_runtime_secrets_are_replaced_outside_production(field_name: str, field_value: str):
    kwargs = {field_name: field_value}
    settings = isolated_settings(**kwargs)
    assert getattr(settings, field_name) != field_value


def test_explicit_data_encryption_key_is_not_marked_as_derived():
    settings = isolated_settings(
        app_env="development",
        app_data_encryption_key="z" * 48,
    )

    assert settings.app_data_encryption_key == "z" * 48
    assert settings.app_data_encryption_key_was_derived is False


def test_development_secret_fallbacks_are_stable_for_same_local_settings():
    first = isolated_settings(
        app_env="development",
        database_url="postgresql+psycopg://postgres:postgres@db:5432/threatlens",
        redis_url="redis://redis:6379/0",
        admin_email="admin@example.com",
    )
    second = isolated_settings(
        app_env="development",
        database_url="postgresql+psycopg://postgres:postgres@db:5432/threatlens",
        redis_url="redis://redis:6379/0",
        admin_email="admin@example.com",
    )

    assert first.jwt_secret == second.jwt_secret
    assert first.app_data_encryption_key == second.app_data_encryption_key
    assert first.jwt_secret != first.app_data_encryption_key
