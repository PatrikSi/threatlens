from app.core.config import Settings
from app.main import _build_openapi_visibility_kwargs


def test_build_openapi_visibility_kwargs_hides_docs_in_production_by_default():
    settings = Settings(
        app_env="production",
        jwt_secret="x" * 48,
        app_data_encryption_key="y" * 48,
        admin_password="StrongPass123!",
        auth_cookie_secure=True,
    )

    assert _build_openapi_visibility_kwargs(settings) == {
        "docs_url": None,
        "redoc_url": None,
        "openapi_url": None,
    }


def test_build_openapi_visibility_kwargs_allows_opt_in_docs_in_production():
    settings = Settings(
        app_env="production",
        jwt_secret="x" * 48,
        app_data_encryption_key="y" * 48,
        admin_password="StrongPass123!",
        auth_cookie_secure=True,
        expose_api_docs_in_production=True,
    )

    assert _build_openapi_visibility_kwargs(settings) == {}


def test_build_openapi_visibility_kwargs_keeps_docs_in_development():
    settings = Settings(app_env="development")

    assert _build_openapi_visibility_kwargs(settings) == {}
