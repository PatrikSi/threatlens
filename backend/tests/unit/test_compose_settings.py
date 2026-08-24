import re
from pathlib import Path

from app.core.config import Settings


ROOT = Path(__file__).resolve().parents[3]
ENVIRONMENT_LINE_PATTERN = re.compile(r"^([A-Z][A-Z0-9_]*)=", re.MULTILINE)
DOCUMENTED_SETTING_PATTERN = re.compile(
    r"^\| `([A-Z][A-Z0-9_]*)` \(`([a-z][a-z0-9_]*)`\) \|",
    re.MULTILINE,
)

# These variables configure bundled services, images, or the web build rather
# than app.core.config.Settings. Keeping the reason beside each exception makes
# additions to the public environment inventory deliberate and reviewable.
NON_BACKEND_ENVIRONMENT_NAMES = {
    "AI_WORKER_CONCURRENCY": "Celery worker process concurrency",
    "POSTGRES_DB": "bundled PostgreSQL service database",
    "POSTGRES_USER": "bundled PostgreSQL service role",
    "THREATLENS_CSP_CONNECT_SRC": "web container Content-Security-Policy",
    "THREATLENS_CSP_FRAME_SRC": "web container Content-Security-Policy",
    "THREATLENS_IMAGE_TAG": "published container image selection",
    "THREATLENS_WEB_PORT": "host-to-web container port mapping",
    "WEB_VITE_API_BASE_URL": "web build API base path",
}


def test_compose_forwards_every_backend_setting():
    compose_text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    mapped_environment_names = set(re.findall(r"^  ([A-Z][A-Z0-9_]+):", compose_text, re.MULTILINE))
    settings_environment_names = {field_name.upper() for field_name in Settings.model_fields}

    assert settings_environment_names - mapped_environment_names - {"POSTGRES_PASSWORD", "REDIS_PASSWORD"} == set()


def test_env_example_inventories_every_backend_setting():
    env_text = (ROOT / ".env.example").read_text(encoding="utf-8")
    environment_names = ENVIRONMENT_LINE_PATTERN.findall(env_text)
    settings_environment_names = {
        field_name.upper() for field_name in Settings.model_fields
    }

    assert len(environment_names) == len(set(environment_names))
    assert set(environment_names) == (
        settings_environment_names | set(NON_BACKEND_ENVIRONMENT_NAMES)
    )
    assert all(reason.strip() for reason in NON_BACKEND_ENVIRONMENT_NAMES.values())


def test_configuration_reference_inventories_every_backend_setting():
    documentation = (ROOT / "docs/reference/configuration.md").read_text(
        encoding="utf-8"
    )
    documented_settings = DOCUMENTED_SETTING_PATTERN.findall(documentation)
    expected_settings = {
        (field_name.upper(), field_name) for field_name in Settings.model_fields
    }

    assert len(documented_settings) == len(set(documented_settings))
    assert set(documented_settings) == expected_settings
