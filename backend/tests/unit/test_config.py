import pytest

from app.core.config import Settings


def test_cors_origins_parses_csv():
    settings = Settings(cors_origins="http://localhost:3000, https://threatlens.local")
    assert settings.cors_origins == ["http://localhost:3000", "https://threatlens.local"]


def test_production_requires_strong_jwt_secret():
    with pytest.raises(ValueError):
        Settings(app_env="production", jwt_secret="change-me")


def test_production_rejects_default_admin_password():
    with pytest.raises(ValueError):
        Settings(app_env="production", jwt_secret="x" * 48, admin_password="admin123")
