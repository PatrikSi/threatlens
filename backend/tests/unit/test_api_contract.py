import json
from pathlib import Path
import re

from fastapi.testclient import TestClient

from app.main import API_SERVICE_PREFIX, OPENAPI_PROXY_PATH, WEB_PROXY_API_PREFIX, app
from app.services.api_contract import build_openapi_schema_document, render_api_reference_markdown

REPO_ROOT = Path(__file__).resolve().parents[3]
API_REFERENCE_PATH = REPO_ROOT / "docs" / "reference" / "api.md"
OPENAPI_SCHEMA_PATH = REPO_ROOT / "docs" / "reference" / "openapi.json"
FRONTEND_TYPES_PATH = REPO_ROOT / "web" / "src" / "types" / "api.ts"


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
    assert "- Machine-readable OpenAPI schema on the API service: `/openapi.json`" in reference
    assert f"- Machine-readable OpenAPI schema through the web proxy: `{OPENAPI_PROXY_PATH}`" in reference
    assert "`ApiTokenBearer`: `http`" in reference
    assert "`SessionCookieAuth`: `apiKey`" in reference
    assert "/v1/auth/login" in reference
    assert "/api/v1/auth/login" in reference


def test_generated_openapi_document_matches_live_schema():
    client = TestClient(app)

    response = client.get("/openapi.json")
    generated = build_openapi_schema_document(app)

    assert response.status_code == 200
    assert json.loads(generated) == response.json()
    assert response.json()["info"]["license"]["name"] == "Apache-2.0"
    assert "contact" not in response.json()["info"]


def test_openapi_operations_preserve_required_token_scope_extensions():
    schema = app.openapi()

    assert schema["paths"]["/v1/feeds"]["get"]["x-threatlens-required-token-scopes"] == ["read:feeds"]
    assert schema["paths"]["/v1/feeds"]["post"]["x-threatlens-required-token-scopes"] == ["write:feeds"]


def test_openapi_preserves_saved_view_query_component_names():
    schemas = app.openapi()["components"]["schemas"]

    assert "SavedViewQueryPayload" not in schemas
    assert schemas["SavedViewQueryPayload-Input"] == schemas["SavedViewQueryPayload-Output"]
    assert schemas["SavedViewCreate"]["properties"]["query_json"]["$ref"].endswith(
        "/SavedViewQueryPayload-Input"
    )
    assert schemas["SavedViewUpdate"]["properties"]["query_json"]["anyOf"][0]["$ref"].endswith(
        "/SavedViewQueryPayload-Input"
    )
    assert schemas["SavedViewResponse"]["properties"]["query_json"]["$ref"].endswith(
        "/SavedViewQueryPayload-Output"
    )


def test_checked_in_api_reference_matches_generated_reference():
    generated = render_api_reference_markdown(
        app,
        service_base_path=API_SERVICE_PREFIX,
        proxy_base_path=WEB_PROXY_API_PREFIX,
        openapi_service_path="/openapi.json",
        openapi_proxy_path=OPENAPI_PROXY_PATH,
    )

    assert API_REFERENCE_PATH.read_text(encoding="utf-8") == generated


def test_checked_in_openapi_document_matches_generated_schema():
    assert OPENAPI_SCHEMA_PATH.read_text(encoding="utf-8") == build_openapi_schema_document(app)


def test_frontend_notification_delivery_type_includes_not_before_contract_field():
    delivery_schema = app.openapi()["components"]["schemas"]["NotificationWebhookDeliveryResponse"]

    assert "not_before" in delivery_schema["properties"]
    delivery_type = FRONTEND_TYPES_PATH.read_text(encoding="utf-8")
    match = re.search(r"export interface NotificationWebhookDelivery \{(?P<body>.*?)\n\}", delivery_type, re.DOTALL)
    assert match is not None
    assert "not_before: string | null" in match.group("body")


def test_reference_docs_directory_still_exists():
    assert (REPO_ROOT / "docs" / "reference").is_dir()
