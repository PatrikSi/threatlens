from pathlib import Path

from app.main import API_SERVICE_PREFIX, OPENAPI_PROXY_PATH, WEB_PROXY_API_PREFIX, app
from app.services.api_contract import build_openapi_schema_document, render_api_reference_markdown

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_generated_api_reference_is_current():
    expected = render_api_reference_markdown(
        app,
        service_base_path=API_SERVICE_PREFIX,
        proxy_base_path=WEB_PROXY_API_PREFIX,
        openapi_service_path="/openapi.json",
        openapi_proxy_path=OPENAPI_PROXY_PATH,
    )

    assert (REPO_ROOT / "docs" / "reference" / "api.md").read_text(encoding="utf-8") == expected


def test_generated_openapi_snapshot_is_current():
    expected = build_openapi_schema_document(app)

    assert (REPO_ROOT / "docs" / "reference" / "openapi.json").read_text(encoding="utf-8") == expected
