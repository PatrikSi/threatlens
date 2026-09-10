import re
from pathlib import Path

import yaml

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
    "POSTGRES_RUNTIME_USER": "runtime PostgreSQL DML role",
    "POSTGRES_RUNTIME_PASSWORD": "runtime PostgreSQL credential provisioning",
    "POSTGRES_MIGRATION_USER": "migration PostgreSQL owner role",
    "POSTGRES_MIGRATION_PASSWORD": "migration PostgreSQL credential provisioning",
    "MIGRATION_DATABASE_URL": "one-shot migration connection",
    "API_DATABASE_POOL_SIZE": "API per-process connection allocation",
    "API_DATABASE_MAX_OVERFLOW": "API temporary connection allocation",
    "EXPORT_DATABASE_POOL_SIZE": "export worker connection allocation including lease renewals",
    "DB_CPUS": "container CPU quota",
    "DB_MEMORY": "container memory ceiling including temporary files",
    "REDIS_CPUS": "container CPU quota",
    "REDIS_MEMORY": "container memory ceiling including temporary files",
    "API_CPUS": "container CPU quota",
    "API_MEMORY": "container memory ceiling including temporary files",
    "WORKER_CPUS": "container CPU quota",
    "WORKER_MEMORY": "container memory ceiling including temporary files",
    "EXPORT_WORKER_CPUS": "container CPU quota",
    "EXPORT_WORKER_MEMORY": "container memory ceiling including temporary files",
    "AI_WORKER_CPUS": "container CPU quota",
    "AI_WORKER_MEMORY": "container memory ceiling including temporary files",
    "MAINTENANCE_WORKER_CPUS": "container CPU quota",
    "MAINTENANCE_WORKER_MEMORY": "container memory ceiling including temporary files",
    "NOTIFICATION_WORKER_CPUS": "container CPU quota",
    "NOTIFICATION_WORKER_MEMORY": "container memory ceiling including temporary files",
    "BEAT_CPUS": "container CPU quota",
    "BEAT_MEMORY": "container memory ceiling including temporary files",
    "WEB_CPUS": "container CPU quota",
    "WEB_MEMORY": "container memory ceiling including temporary files",
    "AI_WORKER_CONCURRENCY": "Celery worker process concurrency",
    "EXPORT_WORKER_CONCURRENCY": "Celery export worker concurrency",
    "MAINTENANCE_WORKER_CONCURRENCY": "Celery maintenance worker concurrency",
    "NOTIFICATION_WORKER_CONCURRENCY": "Celery notification worker concurrency",
    "POSTGRES_DB": "bundled PostgreSQL service database",
    "POSTGRES_USER": "bundled PostgreSQL service role",
    "THREATLENS_CSP_CONNECT_SRC": "web container Content-Security-Policy",
    "THREATLENS_CSP_FRAME_SRC": "web container Content-Security-Policy",
    "THREATLENS_DEV_IMAGE_TAG": "locally built container image selection",
    "THREATLENS_IMAGE_TAG": "published container image selection",
    "THREATLENS_WEB_PORT": "host-to-web container port mapping",
    "WEB_VITE_API_BASE_URL": "web build API base path",
    "WORKER_CONCURRENCY": "Celery general worker concurrency",
}
EPHEMERAL_BUILD_ENVIRONMENT_NAMES = {
    "BUILD_DATE",
    "THREATLENS_BUILD_VERSION",
    "VCS_REF",
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


def test_source_build_version_ignores_runtime_app_version_override():
    compose_text = (ROOT / "docker-compose.build.yml").read_text(encoding="utf-8")
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()

    assert "${APP_VERSION" not in compose_text
    assert compose_text.count(
        f"APP_VERSION: ${{THREATLENS_BUILD_VERSION:-{version}}}"
    ) == 2


def test_ai_worker_consumes_the_versioned_report_queue():
    compose_text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert '"--queues=ai,ai-reports-v2"' in compose_text
    assert "{'ai', 'ai-reports-v2'} <= names" in compose_text


def test_maintenance_worker_consumes_and_health_checks_the_versioned_lifecycle_queue():
    compose_text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert '"--queues=maintenance,lifecycle-v1"' in compose_text
    assert "{'maintenance', 'lifecycle-v1'} <= names" in compose_text


def test_lifecycle_queue_cutover_documents_the_quiescence_boundary():
    documentation = (ROOT / "docs/reference/configuration.md").read_text(
        encoding="utf-8"
    )

    assert "## Lifecycle Queue Cutover" in documentation
    assert "docker compose stop beat api" in documentation
    assert "docker compose up -d --wait beat" in documentation
    assert "After catalog bootstrap, do not start" in documentation
    assert "maintenance consumer against that database" in documentation


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


def test_runtime_services_are_constrained_and_do_not_receive_admin_credentials():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    services = compose["services"]
    for name, service in services.items():
        assert service["read_only"] is True, name
        assert service["cap_drop"] == ["ALL"], name
        assert "no-new-privileges:true" in service["security_opt"], name
        assert service["pids_limit"] > 0, name
        assert service["cpus"] and service["mem_limit"], name
        assert service["memswap_limit"] == service["mem_limit"], name
        if name not in {"db", "redis"}:
            assert not service.get("cap_add"), name
        if name not in {"db", "migrate"}:
            env = service.get("environment", {})
            assert "POSTGRES_PASSWORD" not in env, name
            assert "POSTGRES_MIGRATION_PASSWORD" not in env, name
            assert "MIGRATION_DATABASE_URL" not in env, name
    assert services["api"]["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    assert services["migrate"]["command"] == ["alembic", "upgrade", "head"]
    assert "POSTGRES_MIGRATION_PASSWORD" in services["migrate"]["environment"]["DATABASE_URL"]
    assert "POSTGRES_RUNTIME_PASSWORD" in services["api"]["environment"]["DATABASE_URL"]


def test_export_resource_budget_preserves_processing_cpu_priority():
    services = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]
    assert services["worker-exports"]["cpu_shares"] < services["worker"]["cpu_shares"]
    assert services["worker-exports"]["cpu_shares"] < services["api"]["cpu_shares"]
    assert "/tmp:rw,noexec,nosuid,size=1g,mode=1777" in services["worker-exports"]["tmpfs"]


def test_release_smoke_pins_every_backend_service_including_migrations():
    # Read the generated override as YAML: omitted services would silently pull
    # a tag unrelated to the exact candidate image under release verification.
    workflow = yaml.safe_load((ROOT / ".github/workflows/publish-images.yml").read_text())
    commands = [step.get("run", "") for job in workflow["jobs"].values() for step in job.get("steps", [])]
    script = next(command for command in commands if 'release-images.yml" <<EOF' in command)
    override = yaml.safe_load(script.split('release-images.yml" <<EOF\n', 1)[1].split('\nEOF', 1)[0])
    services = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]
    backend = {name for name in services if name not in {"web", "db", "redis"}}
    assert backend <= override["services"].keys()
    for name in backend:
        assert override["services"][name]["image"] == "${backend_ref}"
        assert override["services"][name]["platform"] == "${{ matrix.platform }}"
