import re
from pathlib import Path

from app.core.config import Settings


ROOT = Path(__file__).resolve().parents[3]
ENVIRONMENT_LINE_PATTERN = re.compile(r"^([A-Z][A-Z0-9_]*)=", re.MULTILINE)
COMPOSE_SUBSTITUTION_PATTERN = re.compile(r"\$\{([A-Z][A-Z0-9_]*)")
DOCUMENTED_SETTING_PATTERN = re.compile(
    r"^\| `([A-Z][A-Z0-9_]*)` \(`([a-z][a-z0-9_]*)`\) \|",
    re.MULTILINE,
)

# These variables configure bundled services, images, or the web build rather
# than app.core.config.Settings. Keeping the reason beside each exception makes
# additions to the public environment inventory deliberate and reviewable.
NON_BACKEND_ENVIRONMENT_NAMES = {
    "APP_VERSION": "local image build version label",
    "AI_WORKER_CONCURRENCY": "Celery worker process concurrency",
    "MAINTENANCE_WORKER_CONCURRENCY": "Celery maintenance worker concurrency",
    "NOTIFICATION_WORKER_CONCURRENCY": "Celery notification worker concurrency",
    "POSTGRES_DB": "bundled PostgreSQL service database",
    "POSTGRES_USER": "bundled PostgreSQL service role",
    "THREATLENS_CSP_CONNECT_SRC": "web container Content-Security-Policy",
    "THREATLENS_CSP_FRAME_SRC": "web container Content-Security-Policy",
    "THREATLENS_IMAGE_TAG": "published container image selection",
    "THREATLENS_WEB_PORT": "host-to-web container port mapping",
    "WEB_VITE_API_BASE_URL": "web build API base path",
    "WORKER_CONCURRENCY": "Celery general worker concurrency",
}
EPHEMERAL_BUILD_ENVIRONMENT_NAMES = {"BUILD_DATE", "VCS_REF"}


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


def test_compose_substitutions_are_inventoried_in_env_example():
    compose_environment_names: set[str] = set()
    for compose_path in ROOT.glob("docker-compose*.yml"):
        compose_environment_names.update(
            COMPOSE_SUBSTITUTION_PATTERN.findall(
                compose_path.read_text(encoding="utf-8")
            )
        )
    env_text = (ROOT / ".env.example").read_text(encoding="utf-8")
    environment_names = set(ENVIRONMENT_LINE_PATTERN.findall(env_text))

    assert compose_environment_names - environment_names == (
        EPHEMERAL_BUILD_ENVIRONMENT_NAMES
    )


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
