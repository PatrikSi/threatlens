import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import API_SERVICE_PREFIX, OPENAPI_PROXY_PATH, WEB_PROXY_API_PREFIX, app
from app.services.api_contract import build_openapi_schema_document, render_api_reference_markdown

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_api_reference_markdown_describes_published_auth_contract():
    reference = render_api_reference_markdown(
        app,
        service_base_path=API_SERVICE_PREFIX,
        proxy_base_path=WEB_PROXY_API_PREFIX,
        openapi_service_path="/openapi.json",
        openapi_proxy_path=OPENAPI_PROXY_PATH,
    )

    assert f"- API service base path: `{API_SERVICE_PREFIX}`" in reference
    assert f"- Web proxy base path: `{WEB_PROXY_API_PREFIX}`" in reference
    assert f"- Machine-readable OpenAPI schema on the API service: `/openapi.json`" in reference
    assert f"- Machine-readable OpenAPI schema through the web proxy: `{OPENAPI_PROXY_PATH}`" in reference
    assert "`ApiTokenBearer`: `http`" in reference
    assert "`SessionCookieAuth`: `apiKey`" in reference
    assert "/v1/auth/login" in reference
    assert "/api/v1/auth/login" in reference


def test_generated_openapi_document_matches_live_schema():
    client = TestClient(app)

    response = client.get("/openapi.json")

    assert response.status_code == 200
    assert json.loads(build_openapi_schema_document(app)) == response.json()


def test_reference_docs_directory_still_exists():
    assert (REPO_ROOT / "docs" / "reference").is_dir()
