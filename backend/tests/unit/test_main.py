from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import app, _build_openapi_visibility_kwargs


def test_build_openapi_visibility_kwargs_hides_docs_ui_in_production_by_default():
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


def test_versioned_routes_are_published_while_legacy_routes_remain_available():
    client = TestClient(app)

    versioned = client.get("/v1/health/live")
    legacy = client.get("/health/live")
    schema = client.get("/openapi.json")

    assert versioned.status_code == 200
    assert legacy.status_code == 200
    assert schema.status_code == 200
    payload = schema.json()
    assert "/v1/health/live" in payload["paths"]
    assert "/health/live" not in payload["paths"]


def test_live_schema_publishes_versioned_auth_contract():
    client = TestClient(app)

    response = client.get("/openapi.json")

    assert response.status_code == 200
    payload = response.json()
    api_token_scheme = payload["components"]["securitySchemes"]["ApiTokenBearer"]
    session_cookie_scheme = payload["components"]["securitySchemes"]["SessionCookieAuth"]
    assert api_token_scheme["type"] == "http"
    assert api_token_scheme["scheme"] == "bearer"
    assert "scoped personal API token" in api_token_scheme["description"]
    assert session_cookie_scheme["type"] == "apiKey"
    assert session_cookie_scheme["in"] == "cookie"
    assert session_cookie_scheme["name"] == "threatlens_session"
    assert "/api/v1/auth/login" in session_cookie_scheme["description"]
    assert "HttpOnly cookie sessions" in payload["info"]["description"]
