#!/usr/bin/env python3
"""Safety primitives for the host-side ThreatLens recovery workflow."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import stat
import sys
from pathlib import Path
from typing import Any, BinaryIO, NoReturn
from urllib.parse import quote, unquote, urlsplit


MAX_COMPOSE_CONFIG_BYTES = 16 * 1024 * 1024
COPY_CHUNK_BYTES = 1024 * 1024
SUPPORTED_APP_SERVICES = (
    "api",
    "worker",
    "worker-ai",
    "worker-maintenance",
    "worker-notifications",
    "beat",
)


class RecoverySafetyError(Exception):
    """A bounded, operator-facing safety validation failure."""


def _fail(message: str) -> NoReturn:
    raise RecoverySafetyError(message)


def _read_compose_document() -> dict[str, Any]:
    raw = sys.stdin.buffer.read(MAX_COMPOSE_CONFIG_BYTES + 1)
    if len(raw) > MAX_COMPOSE_CONFIG_BYTES:
        _fail("Compose configuration exceeds the safety limit")
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        _fail(f"Compose configuration is not valid UTF-8 JSON: {error}")
    if not isinstance(document, dict) or not isinstance(document.get("services"), dict):
        _fail("Compose configuration has no services object")
    return document


def _service(document: dict[str, Any], name: str) -> dict[str, Any]:
    value = document["services"].get(name)
    if not isinstance(value, dict):
        _fail(f"Compose service {name!r} is unavailable")
    return value


def _environment(service: dict[str, Any], *, service_name: str) -> dict[str, str]:
    raw = service.get("environment", {})
    if isinstance(raw, dict):
        environment: dict[str, str] = {}
        for key, value in raw.items():
            if not isinstance(key, str) or value is None:
                continue
            if not isinstance(value, (str, int, float, bool)):
                _fail(
                    f"Compose service {service_name!r} has an invalid environment entry"
                )
            environment[key] = str(value)
        return environment
    if isinstance(raw, list):
        environment = {}
        for entry in raw:
            if not isinstance(entry, str) or "=" not in entry:
                _fail(
                    f"Compose service {service_name!r} has an unresolved environment entry"
                )
            key, value = entry.split("=", 1)
            environment[key] = value
        return environment
    _fail(f"Compose service {service_name!r} has an invalid environment block")


def _networks(service: dict[str, Any]) -> set[str]:
    raw = service.get("networks", {})
    if isinstance(raw, dict):
        return {name for name in raw if isinstance(name, str)}
    if isinstance(raw, list):
        return {name for name in raw if isinstance(name, str)}
    return set()


def _required_environment_value(
    environment: dict[str, str], key: str, service: str
) -> str:
    value = environment.get(key)
    if not value:
        _fail(f"Compose service {service!r} has no rendered {key}")
    if any(character in value for character in "\r\n\0"):
        _fail(f"Compose service {service!r} has an invalid rendered {key}")
    return value


def _parse_port(parts, default: int, *, label: str) -> int:
    try:
        return parts.port or default
    except ValueError as error:
        _fail(f"{label} has an invalid port: {error}")


def _validate_database_url(
    value: str,
    *,
    expected_user: str,
    expected_password: str,
    expected_database: str,
    service_name: str,
) -> None:
    try:
        parts = urlsplit(value)
    except ValueError as error:
        _fail(f"Compose service {service_name!r} DATABASE_URL is invalid: {error}")
    if parts.scheme not in {"postgresql", "postgresql+psycopg"}:
        _fail(
            f"Compose service {service_name!r} DATABASE_URL must use the local PostgreSQL adapter"
        )
    if (parts.hostname or "").lower() != "db" or _parse_port(
        parts, 5432, label="DATABASE_URL"
    ) != 5432:
        _fail(
            f"Compose service {service_name!r} DATABASE_URL does not target local service db:5432"
        )
    if unquote(parts.username or "") != expected_user:
        _fail(
            f"Compose service {service_name!r} DATABASE_URL user differs from POSTGRES_USER"
        )
    if unquote(parts.password or "") != expected_password:
        _fail(
            f"Compose service {service_name!r} DATABASE_URL password differs from the db service"
        )
    if unquote(parts.path.lstrip("/")) != expected_database:
        _fail(
            f"Compose service {service_name!r} DATABASE_URL database differs from POSTGRES_DB"
        )
    if parts.query or parts.fragment:
        _fail(
            f"Compose service {service_name!r} DATABASE_URL options require an external recovery adapter"
        )


def _validate_redis_url(
    value: str,
    *,
    expected_password: str,
    service_name: str,
) -> None:
    try:
        parts = urlsplit(value)
    except ValueError as error:
        _fail(f"Compose service {service_name!r} REDIS_URL is invalid: {error}")
    if parts.scheme != "redis":
        _fail(
            f"Compose service {service_name!r} REDIS_URL must use the local Redis adapter"
        )
    if (parts.hostname or "").lower() != "redis" or _parse_port(
        parts, 6379, label="REDIS_URL"
    ) != 6379:
        _fail(
            f"Compose service {service_name!r} REDIS_URL does not target local service redis:6379"
        )
    if unquote(parts.username or "") not in {"", "default"}:
        _fail(f"Compose service {service_name!r} REDIS_URL has an unsupported username")
    if unquote(parts.password or "") != expected_password:
        _fail(
            f"Compose service {service_name!r} REDIS_URL password differs from the redis service"
        )
    database_text = parts.path.lstrip("/") or "0"
    if database_text != "0":
        _fail(
            f"Compose service {service_name!r} REDIS_URL selects database {database_text}; "
            "only local database 0 is supported"
        )
    if parts.query or parts.fragment:
        _fail(
            f"Compose service {service_name!r} REDIS_URL options require an external recovery adapter"
        )


def _command_validate_target(_args: argparse.Namespace) -> None:
    document = _read_compose_document()
    db_service = _service(document, "db")
    redis_service = _service(document, "redis")
    db_environment = _environment(db_service, service_name="db")
    redis_environment = _environment(redis_service, service_name="redis")
    database = _required_environment_value(db_environment, "POSTGRES_DB", "db")
    database_user = _required_environment_value(db_environment, "POSTGRES_USER", "db")
    database_password = _required_environment_value(
        db_environment, "POSTGRES_PASSWORD", "db"
    )
    redis_password = _required_environment_value(
        redis_environment, "REDIS_PASSWORD", "redis"
    )
    if not database or database in {"postgres", "template0", "template1"}:
        _fail("POSTGRES_DB is not a supported application database name")

    target_networks = _networks(db_service) & _networks(redis_service)
    if not target_networks:
        _fail("Compose db and redis services do not share a network")

    checked_services: list[str] = []
    for service_name, service in document["services"].items():
        if not isinstance(service, dict):
            _fail(f"Compose service {service_name!r} has an invalid definition")
        environment = _environment(service, service_name=service_name)
        has_database_url = bool(environment.get("DATABASE_URL"))
        has_redis_url = bool(environment.get("REDIS_URL"))
        if not has_database_url and not has_redis_url:
            continue
        if service_name not in SUPPORTED_APP_SERVICES:
            _fail(
                f"Compose service {service_name!r} is an unrecognized backend data accessor; "
                "recovery cannot prove that it will be stopped"
            )
        if not has_database_url or not has_redis_url:
            _fail(
                f"Compose service {service_name!r} must declare both DATABASE_URL and REDIS_URL"
            )
        service_networks = _networks(service)
        if not service_networks.intersection(_networks(db_service)):
            _fail(f"Compose service {service_name!r} does not share a network with db")
        if not service_networks.intersection(_networks(redis_service)):
            _fail(
                f"Compose service {service_name!r} does not share a network with redis"
            )
        _validate_database_url(
            _required_environment_value(environment, "DATABASE_URL", service_name),
            expected_user=database_user,
            expected_password=database_password,
            expected_database=database,
            service_name=service_name,
        )
        _validate_redis_url(
            _required_environment_value(environment, "REDIS_URL", service_name),
            expected_password=redis_password,
            service_name=service_name,
        )
        checked_services.append(service_name)

    if "api" not in checked_services:
        _fail(
            "Compose service 'api' is required to validate the application data targets"
        )

    fingerprint_document = {
        "application_services": sorted(checked_services),
        "database": database,
        "database_image": db_service.get("image"),
        "database_networks": sorted(_networks(db_service)),
        "database_user": database_user,
        "redis_image": redis_service.get("image"),
        "redis_networks": sorted(_networks(redis_service)),
    }
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_document, separators=(",", ":"), sort_keys=True).encode(
            "utf-8"
        )
    ).hexdigest()
    print(database)
    print(database_user)
    print("0")
    print(fingerprint)
    print(",".join(sorted(checked_services)))


def _read_json_file(path: Path, *, label: str) -> Any:
    descriptor, metadata = _open_regular(path, label=label)
    if metadata.st_size > MAX_COMPOSE_CONFIG_BYTES:
        os.close(descriptor)
        _fail(f"{label} exceeds the safety limit")
    try:
        with os.fdopen(descriptor, "r", encoding="utf-8", closefd=True) as source:
            return json.load(source)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        _fail(f"{label} is not valid UTF-8 JSON: {error}")


def _inspect_environment(
    container: dict[str, Any], *, service_name: str
) -> dict[str, str]:
    config = container.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    if (
        not isinstance(labels, dict)
        or labels.get("com.docker.compose.service") != service_name
    ):
        _fail(
            f"Running container identity does not match Compose service {service_name!r}"
        )
    raw = config.get("Env")
    if not isinstance(raw, list):
        _fail(
            f"Running container for service {service_name!r} has no inspectable environment"
        )
    environment: dict[str, str] = {}
    for entry in raw:
        if not isinstance(entry, str) or "=" not in entry:
            _fail(
                f"Running container for service {service_name!r} has an invalid environment"
            )
        key, value = entry.split("=", 1)
        environment[key] = value
    return environment


def _command_validate_runtime(args: argparse.Namespace) -> None:
    document = _read_json_file(
        Path(args.compose_config), label="rendered Compose configuration"
    )
    if not isinstance(document, dict) or not isinstance(document.get("services"), dict):
        _fail("Rendered Compose configuration has no services object")
    inspected = _read_json_file(Path(args.inspect), label="Docker inspect document")
    if not isinstance(inspected, list):
        _fail("Docker inspect document must be an array")

    by_service: dict[str, dict[str, Any]] = {}
    for container in inspected:
        if not isinstance(container, dict):
            _fail("Docker inspect document contains an invalid container")
        config = container.get("Config")
        labels = config.get("Labels") if isinstance(config, dict) else None
        service_name = (
            labels.get("com.docker.compose.service")
            if isinstance(labels, dict)
            else None
        )
        if not isinstance(service_name, str) or not service_name:
            _fail("Inspected container has no Compose service identity")
        if service_name in by_service:
            _fail(f"Compose service {service_name!r} resolves to multiple containers")
        by_service[service_name] = container

    for required in ("db", "redis"):
        if required not in by_service:
            _fail(
                f"Running Compose service {required!r} was not included in runtime validation"
            )

    db_rendered = _environment(_service(document, "db"), service_name="db")
    redis_rendered = _environment(_service(document, "redis"), service_name="redis")
    db_runtime = _inspect_environment(by_service["db"], service_name="db")
    redis_runtime = _inspect_environment(by_service["redis"], service_name="redis")
    for key in ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"):
        if db_runtime.get(key) != db_rendered.get(key):
            _fail(
                f"Running db container {key} differs from rendered Compose configuration"
            )
    if redis_runtime.get("REDIS_PASSWORD") != redis_rendered.get("REDIS_PASSWORD"):
        _fail(
            "Running redis container REDIS_PASSWORD differs from rendered Compose configuration"
        )

    for service_name in SUPPORTED_APP_SERVICES:
        service = document["services"].get(service_name)
        if not isinstance(service, dict):
            continue
        rendered = _environment(service, service_name=service_name)
        if not rendered.get("DATABASE_URL") and not rendered.get("REDIS_URL"):
            continue
        container = by_service.get(service_name)
        if container is None:
            continue
        runtime = _inspect_environment(container, service_name=service_name)
        for key in (
            "DATABASE_URL",
            "REDIS_URL",
            "APP_DATA_ENCRYPTION_KEY",
            "APP_DATA_ENCRYPTION_PREVIOUS_KEYS",
        ):
            if key not in rendered:
                continue
            if runtime.get(key) != rendered.get(key):
                _fail(
                    f"Running {service_name} container {key} differs from rendered Compose configuration"
                )

    print("RUNTIME_TARGETS=matched")


def _command_encryption_fingerprint(_args: argparse.Namespace) -> None:
    document = _read_compose_document()
    environment = _environment(_service(document, "api"), service_name="api")
    key = environment.get("APP_DATA_ENCRYPTION_KEY", "")
    if not key:
        print("none")
        return
    print(f"sha256:{hashlib.sha256(key.encode('utf-8')).hexdigest()[:32]}")


def _open_regular(path: Path, *, label: str) -> tuple[int, os.stat_result]:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as error:
        _fail(f"Unable to open {label} without following links: {error}")
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode):
        os.close(descriptor)
        _fail(f"{label} must be a regular file")
    return descriptor, metadata


def _copy_open_file(
    source: BinaryIO,
    destination: Path,
    *,
    mode: int,
) -> tuple[str, int]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(destination, flags, mode)
    digest = hashlib.sha256()
    size = 0
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as output:
            while True:
                chunk = source.read(COPY_CHUNK_BYTES)
                if not chunk:
                    break
                output.write(chunk)
                digest.update(chunk)
                size += len(chunk)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return digest.hexdigest(), size


def _copy_regular(
    source_path: Path,
    destination: Path,
    *,
    label: str,
    mode: int,
) -> tuple[str, int]:
    descriptor, source_metadata = _open_regular(source_path, label=label)
    with os.fdopen(descriptor, "rb", closefd=True) as source:
        digest, size = _copy_open_file(source, destination, mode=mode)
    if size != source_metadata.st_size:
        destination.unlink(missing_ok=True)
        _fail(f"{label} changed size while it was being staged")
    return digest, size


def _secure_destination(path: Path) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        _fail("Private staging directory does not exist")
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        _fail("Private staging destination must be a real directory")
    if stat.S_IMODE(metadata.st_mode) != 0o700:
        _fail("Private staging destination must have mode 0700")
    if metadata.st_uid != os.geteuid():
        _fail("Private staging destination must be owned by the invoking user")
    if any(path.iterdir()):
        _fail("Private staging destination must be empty")


def _command_stage(args: argparse.Namespace) -> None:
    destination = Path(args.destination)
    _secure_destination(destination)
    manifest_target = destination / "manifest.json"
    archive_target = destination / "database.dump"
    hook_target = destination / "quarantine-hook"
    helper_target = destination / "recovery_manifest.py"

    manifest_digest, _ = _copy_regular(
        Path(args.manifest), manifest_target, label="manifest", mode=0o600
    )
    archive_digest, archive_size = _copy_regular(
        Path(args.archive), archive_target, label="archive", mode=0o600
    )
    hook_digest, _ = _copy_regular(
        Path(args.hook), hook_target, label="quarantine hook", mode=0o700
    )
    helper_digest, _ = _copy_regular(
        Path(args.manifest_helper), helper_target, label="manifest helper", mode=0o700
    )
    if manifest_digest != args.expected_manifest_sha256:
        _fail("Manifest changed after verification and before private staging")
    if archive_digest != args.expected_archive_sha256:
        _fail("Archive changed after verification and before private staging")
    if hook_digest != args.expected_hook_sha256:
        _fail("Quarantine hook changed after approval and before private staging")
    if helper_digest != args.expected_helper_sha256:
        _fail("Manifest helper changed after approval and before private staging")

    directory_descriptor = os.open(
        destination, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    )
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)
    print(manifest_target)
    print(archive_target)
    print(hook_target)
    print(helper_target)
    print(archive_digest)
    print(hook_digest)
    print(helper_digest)
    print(manifest_digest)
    print(archive_size)


def _sha256_regular(path: Path, *, label: str) -> str:
    descriptor, _ = _open_regular(path, label=label)
    digest = hashlib.sha256()
    with os.fdopen(descriptor, "rb", closefd=True) as source:
        for chunk in iter(lambda: source.read(COPY_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _command_sha256(args: argparse.Namespace) -> None:
    print(_sha256_regular(Path(args.path), label="file"))


def _command_identity(args: argparse.Namespace) -> None:
    raw = sys.stdin.buffer.read(MAX_COMPOSE_CONFIG_BYTES + 1)
    if len(raw) > MAX_COMPOSE_CONFIG_BYTES:
        _fail("Deployment identity input exceeds the safety limit")
    values = (
        args.project,
        args.database,
        args.target_config_sha256,
        args.archive_sha256,
        raw.decode("utf-8", errors="strict"),
    )
    if any(
        not value or any(character in value for character in "\r\0") for value in values
    ):
        _fail("Deployment identity contains an invalid or empty component")
    digest = hashlib.sha256("\x1f".join(values).encode("utf-8")).hexdigest()
    print(digest)


def _write_env(path: Path, values: dict[str, str]) -> None:
    if path.exists() or path.is_symlink():
        _fail("Smoke environment destination already exists")
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=True) as output:
            for key in sorted(values):
                value = values[key]
                if any(character in value for character in "\r\n\0"):
                    _fail(
                        f"Smoke environment value {key} contains an unsupported character"
                    )
                output.write(f"{key}={value}\n")
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _command_write_smoke_env(args: argparse.Namespace) -> None:
    document = _read_compose_document()
    api_environment = _environment(_service(document, "api"), service_name="api")
    database_user = os.environ.get("THREATLENS_RECOVERY_DATABASE_USER", "")
    database_password = os.environ.get("THREATLENS_RECOVERY_DATABASE_PASSWORD", "")
    if not database_user or not database_password:
        _fail(
            "Recovery database credentials were not provided to the smoke environment writer"
        )
    if any(character in database_user + database_password for character in "\r\n\0"):
        _fail("Recovery database credentials contain an unsupported character")
    database_url = (
        "postgresql+psycopg://"
        f"{quote(database_user, safe='')}:{quote(database_password, safe='')}@"
        f"{args.database_host}:5432/{quote(args.database, safe='')}"
    )
    values = {
        "ADMIN_EMAIL": "recovery-smoke@invalid.example",
        "ADMIN_PASSWORD": secrets.token_urlsafe(36),
        "AI_ENABLED": "false",
        "ALLOW_PRIVATE_NETWORK_AI": "false",
        "ALLOW_PRIVATE_NETWORK_FETCH": "false",
        "ALLOW_PRIVATE_NETWORK_OIDC": "false",
        "ALLOW_PRIVATE_NETWORK_WEBHOOKS": "false",
        "APP_DATA_ENCRYPTION_KEY": api_environment.get("APP_DATA_ENCRYPTION_KEY", ""),
        "APP_DATA_ENCRYPTION_PREVIOUS_KEYS": api_environment.get(
            "APP_DATA_ENCRYPTION_PREVIOUS_KEYS", ""
        ),
        "APP_ENV": "development",
        "DATABASE_URL": database_url,
        "JWT_SECRET": secrets.token_urlsafe(48),
        "PROBE_FEED_METADATA_ON_CREATE": "false",
        "PROBE_FEED_METADATA_ON_IMPORT": "false",
        "REDIS_URL": "redis://127.0.0.1:1/0",
        "REQUIRE_EXPLICIT_DATA_ENCRYPTION_KEY": "false",
        "RUN_MIGRATIONS_ON_STARTUP": "false",
        "SEED_ADMIN_ON_STARTUP": "false",
    }
    _write_env(Path(args.output), values)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_target = subparsers.add_parser(
        "validate-target", help="validate rendered local Compose data targets"
    )
    validate_target.set_defaults(handler=_command_validate_target)

    validate_runtime = subparsers.add_parser(
        "validate-runtime",
        help="compare rendered and running Compose target environments",
    )
    validate_runtime.add_argument("--compose-config", required=True)
    validate_runtime.add_argument("--inspect", required=True)
    validate_runtime.set_defaults(handler=_command_validate_runtime)

    encryption_fingerprint = subparsers.add_parser(
        "encryption-fingerprint", help="fingerprint the rendered API encryption key"
    )
    encryption_fingerprint.set_defaults(handler=_command_encryption_fingerprint)

    stage = subparsers.add_parser(
        "stage", help="copy approved restore inputs privately"
    )
    stage.add_argument("--manifest", required=True)
    stage.add_argument("--archive", required=True)
    stage.add_argument("--hook", required=True)
    stage.add_argument("--manifest-helper", required=True)
    stage.add_argument("--destination", required=True)
    stage.add_argument("--expected-manifest-sha256", required=True)
    stage.add_argument("--expected-archive-sha256", required=True)
    stage.add_argument("--expected-hook-sha256", required=True)
    stage.add_argument("--expected-helper-sha256", required=True)
    stage.set_defaults(handler=_command_stage)

    sha256 = subparsers.add_parser(
        "sha256", help="hash a regular file without following links"
    )
    sha256.add_argument("--path", required=True)
    sha256.set_defaults(handler=_command_sha256)

    identity = subparsers.add_parser(
        "identity", help="derive a target-bound deployment identity"
    )
    identity.add_argument("--project", required=True)
    identity.add_argument("--database", required=True)
    identity.add_argument("--target-config-sha256", required=True)
    identity.add_argument("--archive-sha256", required=True)
    identity.set_defaults(handler=_command_identity)

    smoke_env = subparsers.add_parser(
        "write-smoke-env", help="write a restrictive current-code smoke environment"
    )
    smoke_env.add_argument("--output", required=True)
    smoke_env.add_argument("--database-host", required=True)
    smoke_env.add_argument("--database", required=True)
    smoke_env.set_defaults(handler=_command_write_smoke_env)
    return parser


def main() -> int:
    try:
        args = _parser().parse_args()
        args.handler(args)
    except RecoverySafetyError as error:
        print(f"ERROR [recovery_safety]: {error}", file=sys.stderr)
        return 4
    except BrokenPipeError:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
