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


def test_production_rejects_default_admin_password():
    with pytest.raises(ValueError):
        Settings(app_env="production", jwt_secret="x" * 48, admin_password="admin123")


def test_production_requires_secure_auth_cookie():
    with pytest.raises(ValueError):
        Settings(
            app_env="production",
            jwt_secret="x" * 48,
            admin_password="StrongPass123!",
            auth_cookie_secure=False,
        )


def test_production_requires_csrf_for_cookie_auth():
    with pytest.raises(ValueError):
        Settings(
            app_env="production",
            jwt_secret="x" * 48,
            admin_password="StrongPass123!",
            auth_cookie_secure=True,
            auth_require_csrf=False,
        )


def test_bootstrap_mutation_flags_default_off():
    settings = Settings()

    assert settings.run_migrations_on_startup is False
    assert settings.seed_admin_on_startup is False
