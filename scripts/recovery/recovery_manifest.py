#!/usr/bin/env python3
"""Create and validate ThreatLens PostgreSQL recovery metadata."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import stat
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, NoReturn


MANIFEST_FORMAT = "threatlens-postgresql-backup"
MANIFEST_SCHEMA_VERSION = 1
ARCHIVE_FILENAME = "database.dump"
ARCHIVE_FORMAT = "postgresql-custom"
TOOL_VERSION = "1"
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_COMPOSE_CONFIG_BYTES = 16 * 1024 * 1024
PARTIAL_PREFIX = ".threatlens-backup.partial."

EXIT_USAGE = 2
EXIT_PREREQUISITE = 3
EXIT_VALIDATION = 4

_APP_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+~-]{0,127}$")
_ALEMBIC_REVISION_RE = re.compile(r"^[A-Za-z0-9_]+(?:,[A-Za-z0-9_]+)*$")
_POSTGRES_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._+()~-]{0,127}$")
_FINGERPRINT_RE = re.compile(r"^sha256:[0-9a-f]{32}$")
_TABLE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_LEDGER_STRING_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+~-]{0,127}$")
_LEDGER_FIELDS = frozenset(
    {
        "alembic_revision",
        "app_version",
        "archive_sha256",
        "archive_size_bytes",
        "catalog_checked",
        "outbound_quarantined",
        "redis_restored",
        "table_count",
        "tool_version",
    }
)
_LEDGER_INTEGER_FIELDS = frozenset({"archive_size_bytes", "table_count"})
_LEDGER_BOOLEAN_FIELDS = frozenset(
    {"catalog_checked", "outbound_quarantined", "redis_restored"}
)


class RecoveryMetadataError(Exception):
    def __init__(self, message: str, *, exit_code: int = EXIT_VALIDATION) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def _fail(message: str, *, exit_code: int = EXIT_VALIDATION) -> NoReturn:
    raise RecoveryMetadataError(message, exit_code=exit_code)


def _reject_symlink_components(path: Path, *, include_leaf: bool) -> None:
    absolute = path.absolute()
    parts = absolute.parts
    current = Path(parts[0])
    final_index = len(parts) if include_leaf else len(parts) - 1
    for part in parts[1:final_index]:
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            if include_leaf and current == absolute:
                return
            if current == absolute.parent and not include_leaf:
                return
            _fail(f"Path component does not exist: {current}")
        if stat.S_ISLNK(mode):
            _fail(f"Symbolic links are not allowed in recovery paths: {current}")


def _ensure_regular_file(path: Path, *, label: str) -> os.stat_result:
    _reject_symlink_components(path, include_leaf=True)
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        _fail(f"{label} does not exist: {path}")
    except OSError as error:
        _fail(f"Unable to open {label} safely: {error}")
    try:
        metadata = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if not stat.S_ISREG(metadata.st_mode):
        _fail(f"{label} must be a regular file: {path}")
    return metadata


def _read_regular_file(path: Path, *, label: str, max_bytes: int | None = None) -> bytes:
    metadata = _ensure_regular_file(path, label=label)
    if max_bytes is not None and metadata.st_size > max_bytes:
        _fail(f"{label} exceeds the {max_bytes}-byte safety limit: {path}")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb", closefd=True) as handle:
        return handle.read()


def _sha256(path: Path) -> str:
    _ensure_regular_file(path, label="archive")
    digest = hashlib.sha256()
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb", closefd=True) as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json_file(path: Path, *, label: str, max_bytes: int) -> Any:
    raw = _read_regular_file(path, label=label, max_bytes=max_bytes)
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        _fail(f"{label} is not valid UTF-8 JSON: {error}")


def _parse_json_object(raw: str, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        _fail(f"{label} is not valid JSON: {error}")
    if not isinstance(value, dict):
        _fail(f"{label} must be a JSON object")
    return value


def _require_object(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(f"{label} must be an object")
    return value


def _require_string(value: Any, *, label: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        _fail(f"{label} has an invalid value")
    return value


def _require_nonnegative_integer(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _fail(f"{label} must be a non-negative integer")
    return value


def _require_utc_timestamp(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        _fail(f"{label} must be an RFC 3339 UTC timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        _fail(f"{label} must be a valid RFC 3339 UTC timestamp")
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        _fail(f"{label} must be in UTC")
    return value


def _validate_counts(value: Any, *, label: str) -> dict[str, int]:
    counts = _require_object(value, label=label)
    validated: dict[str, int] = {}
    for table_name, count in counts.items():
        if not isinstance(table_name, str) or not _TABLE_NAME_RE.fullmatch(table_name):
            _fail(f"{label} contains an invalid table name")
        validated[table_name] = _require_nonnegative_integer(
            count,
            label=f"{label}.{table_name}",
        )
    return validated


def _validate_manifest_document(document: Any) -> dict[str, Any]:
    manifest = _require_object(document, label="manifest")
    if manifest.get("format") != MANIFEST_FORMAT:
        _fail(f"manifest.format must be {MANIFEST_FORMAT!r}")
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        _fail(
            "Unsupported manifest schema_version; expected "
            f"{MANIFEST_SCHEMA_VERSION}"
        )

    _require_string(manifest.get("app_version"), label="manifest.app_version", pattern=_APP_VERSION_RE)
    _require_string(
        manifest.get("alembic_revision"),
        label="manifest.alembic_revision",
        pattern=_ALEMBIC_REVISION_RE,
    )
    _require_string(
        manifest.get("postgresql_version"),
        label="manifest.postgresql_version",
        pattern=_POSTGRES_VERSION_RE,
    )
    _require_utc_timestamp(manifest.get("snapshot_time_utc"), label="manifest.snapshot_time_utc")
    _require_utc_timestamp(manifest.get("metadata_collected_at_utc"), label="manifest.metadata_collected_at_utc")
    if manifest.get("redis_included") is not False:
        _fail("manifest.redis_included must be false")

    fingerprint = manifest.get("encryption_key_fingerprint")
    if fingerprint is not None:
        _require_string(
            fingerprint,
            label="manifest.encryption_key_fingerprint",
            pattern=_FINGERPRINT_RE,
        )

    archive = _require_object(manifest.get("archive"), label="manifest.archive")
    if archive.get("filename") != ARCHIVE_FILENAME:
        _fail(f"manifest.archive.filename must be {ARCHIVE_FILENAME!r}")
    if archive.get("format") != ARCHIVE_FORMAT:
        _fail(f"manifest.archive.format must be {ARCHIVE_FORMAT!r}")
    _require_nonnegative_integer(archive.get("size_bytes"), label="manifest.archive.size_bytes")
    checksum = archive.get("sha256")
    if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum):
        _fail("manifest.archive.sha256 must be a lowercase SHA-256 digest")

    database = _require_object(manifest.get("database"), label="manifest.database")
    _require_nonnegative_integer(database.get("size_bytes"), label="manifest.database.size_bytes")
    if database.get("count_source") != "pg_stat_user_tables_estimate":
        _fail("manifest.database.count_source has an unsupported value")
    _validate_counts(database.get("estimated_row_counts"), label="manifest.database.estimated_row_counts")

    creator = _require_object(manifest.get("created_by"), label="manifest.created_by")
    if creator.get("tool") != "threatlens-recovery":
        _fail("manifest.created_by.tool has an unsupported value")
    if creator.get("tool_version") != TOOL_VERSION:
        _fail("manifest.created_by.tool_version has an unsupported value")
    return manifest


def _atomic_write_json(path: Path, document: dict[str, Any]) -> None:
    _reject_symlink_components(path, include_leaf=False)
    parent = path.parent
    try:
        parent_mode = parent.lstat().st_mode
    except FileNotFoundError:
        _fail(f"Manifest parent directory does not exist: {parent}")
    if stat.S_ISLNK(parent_mode) or not stat.S_ISDIR(parent_mode):
        _fail(f"Manifest parent must be a real directory: {parent}")
    if path.exists() or path.is_symlink():
        _fail(f"Refusing to overwrite existing manifest: {path}")

    payload = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".partial",
        dir=parent,
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        directory_descriptor = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except BaseException:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
        raise


def _completed_manifest_path(raw_path: str) -> Path:
    if "\n" in raw_path or "\r" in raw_path:
        _fail("Recovery paths must not contain line breaks")
    path = Path(raw_path)
    if path.is_dir():
        path /= "manifest.json"
    if path.name != "manifest.json":
        _fail("A completed backup must be a directory or a file named manifest.json")
    if any(part.startswith(PARTIAL_PREFIX) for part in path.absolute().parts):
        _fail("Interrupted partial backup directories cannot be verified or restored")
    return path


def _validated_manifest(raw_path: str) -> tuple[Path, dict[str, Any]]:
    manifest_path = _completed_manifest_path(raw_path)
    document = _load_json_file(
        manifest_path,
        label="manifest",
        max_bytes=MAX_MANIFEST_BYTES,
    )
    return manifest_path, _validate_manifest_document(document)


def _verified_manifest(
    raw_path: str,
    *,
    expected_app_version: str | None = None,
) -> tuple[Path, Path, dict[str, Any]]:
    manifest_path, manifest = _validated_manifest(raw_path)
    if expected_app_version is not None and manifest["app_version"] != expected_app_version:
        _fail(
            "Backup app version does not match this deployment: "
            f"backup={manifest['app_version']} deployment={expected_app_version}"
        )

    archive_path = manifest_path.parent / manifest["archive"]["filename"]
    archive_metadata = _ensure_regular_file(archive_path, label="archive")
    if archive_metadata.st_size != manifest["archive"]["size_bytes"]:
        _fail(
            "Archive size does not match manifest: "
            f"expected={manifest['archive']['size_bytes']} actual={archive_metadata.st_size}"
        )
    actual_checksum = _sha256(archive_path)
    if actual_checksum != manifest["archive"]["sha256"]:
        _fail(
            "Archive SHA-256 does not match manifest: "
            f"expected={manifest['archive']['sha256']} actual={actual_checksum}"
        )
    return manifest_path, archive_path, manifest


def _command_create(args: argparse.Namespace) -> None:
    archive_path = Path(args.archive)
    archive_metadata = _ensure_regular_file(archive_path, label="archive")
    if archive_path.name != ARCHIVE_FILENAME:
        _fail(f"Archive must be named {ARCHIVE_FILENAME!r}")
    if archive_metadata.st_size <= 0:
        _fail("Archive must not be empty")

    estimated_counts = _validate_counts(
        _parse_json_object(args.estimated_counts_json, label="estimated counts"),
        label="estimated counts",
    )
    fingerprint = args.encryption_key_fingerprint or None
    document: dict[str, Any] = {
        "format": MANIFEST_FORMAT,
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "app_version": args.app_version,
        "alembic_revision": args.alembic_revision,
        "postgresql_version": args.postgresql_version,
        "snapshot_time_utc": args.snapshot_time_utc,
        "metadata_collected_at_utc": args.metadata_collected_at_utc,
        "archive": {
            "filename": ARCHIVE_FILENAME,
            "format": ARCHIVE_FORMAT,
            "size_bytes": archive_metadata.st_size,
            "sha256": _sha256(archive_path),
        },
        "database": {
            "size_bytes": args.database_size_bytes,
            "count_source": "pg_stat_user_tables_estimate",
            "estimated_row_counts": estimated_counts,
        },
        "encryption_key_fingerprint": fingerprint,
        "redis_included": False,
        "created_by": {
            "tool": "threatlens-recovery",
            "tool_version": TOOL_VERSION,
        },
    }
    _validate_manifest_document(document)
    _atomic_write_json(Path(args.output), document)


def _command_verify(args: argparse.Namespace) -> None:
    _, archive_path, manifest = _verified_manifest(
        args.backup,
        expected_app_version=args.expected_app_version,
    )
    if args.print_archive_path:
        print(archive_path.absolute())
        return
    print(
        "manifest and checksum verified "
        f"archive={archive_path.absolute()} sha256={manifest['archive']['sha256']}"
    )


def _command_inspect(args: argparse.Namespace) -> None:
    manifest_path, archive_path, manifest = _verified_manifest(
        args.backup,
        expected_app_version=args.expected_app_version,
    )
    values = (
        str(manifest_path.absolute()),
        str(archive_path.absolute()),
        manifest["app_version"],
        manifest["alembic_revision"],
        manifest["archive"]["sha256"],
        str(manifest["archive"]["size_bytes"]),
        manifest["postgresql_version"],
    )
    if any("\n" in value or "\r" in value for value in values):
        _fail("Recovery inspection values must not contain line breaks")
    print("\n".join(values))


def _dotenv_value(path: Path, key: str) -> str | None:
    raw = _read_regular_file(path, label="environment file", max_bytes=MAX_MANIFEST_BYTES)
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        _fail(f"Environment file is not UTF-8: {error}", exit_code=EXIT_PREREQUISITE)
    selected: str | None = None
    for line_number, original in enumerate(lines, start=1):
        line = original.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        name, separator, raw_value = line.partition("=")
        if not separator or name.strip() != key:
            continue
        try:
            parsed = shlex.split(raw_value, comments=True, posix=True)
        except ValueError as error:
            _fail(
                f"Cannot parse {key} in environment file at line {line_number}: {error}",
                exit_code=EXIT_PREREQUISITE,
            )
        if len(parsed) > 1:
            _fail(
                f"Cannot parse {key} in environment file at line {line_number}",
                exit_code=EXIT_PREREQUISITE,
            )
        selected = parsed[0] if parsed else ""
    return selected


def _command_fingerprint_env(args: argparse.Namespace) -> None:
    key = os.environ.get("APP_DATA_ENCRYPTION_KEY")
    if key is None and args.env_file:
        key = _dotenv_value(Path(args.env_file), "APP_DATA_ENCRYPTION_KEY")
    if not key:
        print("none")
        return
    fingerprint = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
    print(f"sha256:{fingerprint}")


def _command_fsync(args: argparse.Namespace) -> None:
    path = Path(args.path)
    metadata = _ensure_regular_file(path, label="file")
    if not stat.S_ISREG(metadata.st_mode):
        _fail(f"Cannot fsync non-regular file: {path}")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _command_fsync_directory(args: argparse.Namespace) -> None:
    path = Path(args.path)
    _reject_symlink_components(path, include_leaf=True)
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        _fail(f"Directory does not exist: {path}")
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        _fail(f"Path must be a real directory: {path}")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _command_compose_image(args: argparse.Namespace) -> None:
    raw = sys.stdin.buffer.read(MAX_COMPOSE_CONFIG_BYTES + 1)
    if len(raw) > MAX_COMPOSE_CONFIG_BYTES:
        _fail("Compose configuration exceeds the safety limit", exit_code=EXIT_PREREQUISITE)
    try:
        document = json.loads(raw.decode("utf-8"))
        image = document["services"][args.service]["image"]
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        _fail(
            f"Unable to resolve Compose image for service {args.service!r}: {error}",
            exit_code=EXIT_PREREQUISITE,
        )
    if (
        not isinstance(image, str)
        or not image
        or image.startswith("-")
        or any(character in image for character in "\r\n\0")
    ):
        _fail(
            f"Compose service {args.service!r} has an invalid image",
            exit_code=EXIT_PREREQUISITE,
        )
    print(image)


def _command_field(args: argparse.Namespace) -> None:
    _, _, manifest = _verified_manifest(args.backup)
    fields: dict[str, Any] = {
        "app_version": manifest["app_version"],
        "alembic_revision": manifest["alembic_revision"],
        "archive_sha256": manifest["archive"]["sha256"],
        "archive_size_bytes": manifest["archive"]["size_bytes"],
        "postgresql_version": manifest["postgresql_version"],
    }
    print(fields[args.name])


def _command_declared_field(args: argparse.Namespace) -> None:
    _, manifest = _validated_manifest(args.backup)
    fields: dict[str, Any] = {
        "archive_sha256": manifest["archive"]["sha256"],
    }
    print(fields[args.name])


def _command_ledger_metadata(args: argparse.Namespace) -> None:
    if len(args.field) > 12:
        _fail("Operation metadata may contain at most 12 fields")
    metadata: dict[str, str | int | bool] = {}
    for raw_field in args.field:
        key, separator, raw_value = raw_field.partition("=")
        if not separator or key not in _LEDGER_FIELDS:
            _fail("Operation metadata contains an unsupported field")
        if key in metadata:
            _fail(f"Operation metadata field is duplicated: {key}")
        if key in _LEDGER_INTEGER_FIELDS:
            try:
                value: str | int | bool = int(raw_value)
            except ValueError:
                _fail(f"Operation metadata field {key} must be an integer")
            if value < 0:
                _fail(f"Operation metadata field {key} must not be negative")
        elif key in _LEDGER_BOOLEAN_FIELDS:
            if raw_value not in {"true", "false"}:
                _fail(f"Operation metadata field {key} must be true or false")
            value = raw_value == "true"
        else:
            if not _LEDGER_STRING_RE.fullmatch(raw_value):
                _fail(f"Operation metadata field {key} has an invalid value")
            value = raw_value
        metadata[key] = value
    print(json.dumps(metadata, separators=(",", ":"), sort_keys=True))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create", help="create an atomic backup manifest")
    create.add_argument("--archive", required=True)
    create.add_argument("--output", required=True)
    create.add_argument("--app-version", required=True)
    create.add_argument("--alembic-revision", required=True)
    create.add_argument("--postgresql-version", required=True)
    create.add_argument("--snapshot-time-utc", required=True)
    create.add_argument("--metadata-collected-at-utc", required=True)
    create.add_argument("--database-size-bytes", type=int, required=True)
    create.add_argument("--estimated-counts-json", required=True)
    create.add_argument("--encryption-key-fingerprint")
    create.set_defaults(handler=_command_create)

    verify = subparsers.add_parser("verify", help="validate a completed backup and checksum")
    verify.add_argument("--backup", required=True)
    verify.add_argument("--expected-app-version")
    verify.add_argument("--print-archive-path", action="store_true")
    verify.set_defaults(handler=_command_verify)

    inspect = subparsers.add_parser(
        "inspect",
        help="verify a backup once and print bounded recovery fields",
    )
    inspect.add_argument("--backup", required=True)
    inspect.add_argument("--expected-app-version")
    inspect.set_defaults(handler=_command_inspect)

    fingerprint = subparsers.add_parser(
        "fingerprint-env",
        help="print only a non-secret encryption-key fingerprint",
    )
    fingerprint.add_argument("--env-file")
    fingerprint.set_defaults(handler=_command_fingerprint_env)

    fsync_file = subparsers.add_parser("fsync", help="fsync a regular file")
    fsync_file.add_argument("--path", required=True)
    fsync_file.set_defaults(handler=_command_fsync)

    fsync_directory = subparsers.add_parser("fsync-directory", help="fsync a directory")
    fsync_directory.add_argument("--path", required=True)
    fsync_directory.set_defaults(handler=_command_fsync_directory)

    compose_image = subparsers.add_parser(
        "compose-image",
        help="read Compose JSON from stdin and print one service image",
    )
    compose_image.add_argument("--service", required=True)
    compose_image.set_defaults(handler=_command_compose_image)

    field = subparsers.add_parser("field", help="print one validated manifest field")
    field.add_argument("--backup", required=True)
    field.add_argument(
        "--name",
        required=True,
        choices=(
            "app_version",
            "alembic_revision",
            "archive_sha256",
            "archive_size_bytes",
            "postgresql_version",
        ),
    )
    field.set_defaults(handler=_command_field)

    declared_field = subparsers.add_parser(
        "declared-field",
        help="read a bounded field from a schema-validated manifest",
    )
    declared_field.add_argument("--backup", required=True)
    declared_field.add_argument(
        "--name",
        required=True,
        choices=("archive_sha256",),
    )
    declared_field.set_defaults(handler=_command_declared_field)

    ledger_metadata = subparsers.add_parser(
        "ledger-metadata",
        help="build bounded allowlisted operation-ledger metadata",
    )
    ledger_metadata.add_argument("--field", action="append", default=[])
    ledger_metadata.set_defaults(handler=_command_ledger_metadata)
    return parser


def main() -> int:
    try:
        args = _parser().parse_args()
        args.handler(args)
    except RecoveryMetadataError as error:
        print(f"ERROR [recovery_metadata]: {error}", file=sys.stderr)
        return error.exit_code
    except BrokenPipeError:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
