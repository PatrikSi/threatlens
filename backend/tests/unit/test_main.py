import re

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import API_SERVICE_PREFIX, app, _build_openapi_visibility_kwargs, _mount_api_routers, _should_mount_legacy_api_aliases


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


def test_should_mount_legacy_api_aliases_only_outside_production():
    assert _should_mount_legacy_api_aliases(Settings(app_env="development")) is True
    assert (
        _should_mount_legacy_api_aliases(
            Settings(
                app_env="production",
                jwt_secret="x" * 48,
                app_data_encryption_key="y" * 48,
                admin_password="StrongPass123!",
                auth_cookie_secure=True,
            )
        )
        is False
    )


def test_mount_api_routers_can_skip_unversioned_aliases():
    isolated_app = FastAPI()
    _mount_api_routers(isolated_app, include_legacy_aliases=False)
    client = TestClient(isolated_app)

    assert client.get(f"{API_SERVICE_PREFIX}/health/live").status_code == 200
    assert client.get("/health/live").status_code == 404


def test_versioned_routes_are_published_while_backend_compatibility_routes_remain_out_of_schema():
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
    assert "publishes only `/api/v1/*` plus `/api/openapi.json`" in payload["info"]["description"]
    assert "contact" not in payload["info"]
    assert re.fullmatch(r"[0-9a-f]{64}", payload["info"]["x-threatlens-contract-sha256"])
