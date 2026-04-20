import pytest

from app.core.config import Settings


def test_cors_origins_parses_csv():
    settings = Settings(cors_origins="http://localhost:3000, https://threatlens.local")
    assert settings.cors_origins == ["http://localhost:3000", "https://threatlens.local"]


def test_cors_origins_parses_csv_from_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000, https://threatlens.local")
    settings = Settings(_env_file=None)
    assert settings.cors_origins == ["http://localhost:3000", "https://threatlens.local"]


def test_trusted_proxy_cidrs_parses_csv_from_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TRUSTED_PROXY_CIDRS", "127.0.0.1/32,::1/128,172.16.0.0/12")
    settings = Settings(_env_file=None)
    assert settings.trusted_proxy_cidrs == ["127.0.0.1/32", "::1/128", "172.16.0.0/12"]


def test_production_requires_strong_jwt_secret():
    with pytest.raises(ValueError):
        Settings(app_env="production", jwt_secret="change-me")


def test_production_requires_dedicated_data_encryption_key():
    with pytest.raises(ValueError):
        Settings(
            app_env="production",
            jwt_secret="x" * 48,
            admin_password="StrongPass123!",
            auth_cookie_secure=True,
            auth_require_csrf=True,
        )


def test_production_rejects_default_admin_password():
    with pytest.raises(ValueError):
        Settings(
            app_env="production",
            jwt_secret="x" * 48,
            app_data_encryption_key="y" * 48,
            admin_password="admin123",
        )


def test_admin_seeding_rejects_default_admin_password():
    with pytest.raises(ValueError):
        Settings(seed_admin_on_startup=True, admin_password="admin123")


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("jwt_secret", "replace-with-long-random-secret"),
        ("app_data_encryption_key", "replace-with-separate-long-random-secret"),
        ("admin_password", "replace-with-strong-admin-password"),
    ],
)
def test_production_rejects_placeholder_secret_values(field_name: str, field_value: str):
    kwargs = {
        "app_env": "production",
        "jwt_secret": "x" * 48,
        "app_data_encryption_key": "y" * 48,
        "admin_password": "StrongPass123!",
        "auth_cookie_secure": True,
        "auth_require_csrf": True,
    }
    kwargs[field_name] = field_value
    with pytest.raises(ValueError):
        Settings(**kwargs)


def test_production_requires_secure_auth_cookie():
    with pytest.raises(ValueError):
        Settings(
            app_env="production",
            jwt_secret="x" * 48,
            app_data_encryption_key="y" * 48,
            admin_password="StrongPass123!",
            auth_cookie_secure=False,
        )


def test_production_requires_csrf_for_cookie_auth():
    with pytest.raises(ValueError):
        Settings(
            app_env="production",
            jwt_secret="x" * 48,
            app_data_encryption_key="y" * 48,
            admin_password="StrongPass123!",
            auth_cookie_secure=True,
            auth_require_csrf=False,
        )


def test_production_rejects_legacy_unscoped_token_bypass():
    with pytest.raises(ValueError):
        Settings(
            app_env="production",
            jwt_secret="x" * 48,
            app_data_encryption_key="y" * 48,
            admin_password="StrongPass123!",
            auth_cookie_secure=True,
            auth_require_csrf=True,
            allow_legacy_unscoped_tokens=True,
        )


def test_bootstrap_mutation_flags_default_off():
    settings = Settings()

    assert settings.run_migrations_on_startup is False
    assert settings.seed_admin_on_startup is False


def test_development_generates_runtime_secrets_when_not_configured():
    settings = Settings()

    assert settings.jwt_secret
    assert settings.app_data_encryption_key
    assert settings.jwt_secret != settings.app_data_encryption_key
    assert settings.jwt_secret != "change-me"


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("jwt_secret", "change-me"),
        ("app_data_encryption_key", "change-me"),
    ],
)
def test_placeholder_runtime_secrets_are_replaced_outside_production(field_name: str, field_value: str):
    kwargs = {field_name: field_value}
    settings = Settings(**kwargs)
    assert getattr(settings, field_name) != field_value
