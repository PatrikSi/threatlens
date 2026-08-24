import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

# Checked-in API artifacts describe the source tree, not a surrounding runtime.
os.environ["APP_VERSION"] = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()

from app.main import API_SERVICE_PREFIX, OPENAPI_PROXY_PATH, WEB_PROXY_API_PREFIX, app  # noqa: E402
from app.services.api_contract import (  # noqa: E402
    build_openapi_schema_document,
    render_api_reference_markdown,
)

REFERENCE_DIR = REPO_ROOT / "docs" / "reference"
API_REFERENCE_PATH = REFERENCE_DIR / "api.md"
OPENAPI_SCHEMA_PATH = REFERENCE_DIR / "openapi.json"


def main() -> None:
    API_REFERENCE_PATH.write_text(
        render_api_reference_markdown(
            app,
            service_base_path=API_SERVICE_PREFIX,
            proxy_base_path=WEB_PROXY_API_PREFIX,
            openapi_service_path="/openapi.json",
            openapi_proxy_path=OPENAPI_PROXY_PATH,
        ),
        encoding="utf-8",
    )
    OPENAPI_SCHEMA_PATH.write_text(build_openapi_schema_document(app), encoding="utf-8")
    print(f"updated {API_REFERENCE_PATH.relative_to(REPO_ROOT)}")
    print(f"updated {OPENAPI_SCHEMA_PATH.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
