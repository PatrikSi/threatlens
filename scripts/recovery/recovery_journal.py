#!/usr/bin/env python3
"""Durable, private phase journal for destructive ThreatLens recovery."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import stat
import sys
from pathlib import Path
from typing import Any, NoReturn


MAX_JOURNAL_BYTES = 64 * 1024
JOURNAL_VERSION = 1
ACTIVE_DIRECTORY = "active"
JOURNAL_FILENAME = "journal.json"
HISTORY_DIRECTORY = "history"
INIT_DIRECTORY_PREFIX = ".active.init."
INIT_DIRECTORY_SUFFIX = ".partial"
ARCHIVE_DIRECTORY_PREFIX = ".active.archive."
ARCHIVE_DIRECTORY_SUFFIX = ".pending"
ARCHIVE_RECEIPT_FILENAME = "last-archived.json"
ARCHIVE_RECEIPT_TEMPORARY_PREFIX = f".{ARCHIVE_RECEIPT_FILENAME}."
TEST_FAILPOINT_ENV = "THREATLENS_RECOVERY_JOURNAL_TEST_FAILPOINT"
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_OID = re.compile(r"^[1-9][0-9]{0,19}$")
_PHASE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_STATUS = {"running", "succeeded", "failed", "unknown"}
_OUTCOME = {"pending", "rolled_back", "forward_committed", "unknown"}


class JournalError(Exception):
    """A bounded, operator-facing journal failure."""


def _fail(message: str) -> NoReturn:
    raise JournalError(message)


def _reject_symlink_components(path: Path, *, allow_missing_leaf: bool) -> None:
    absolute = path.absolute()
    parts = absolute.parts
    current = Path(parts[0])
    for index, part in enumerate(parts[1:], start=1):
        current /= part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            if allow_missing_leaf and index == len(parts) - 1:
                return
            _fail(f"Journal path component does not exist: {current}")
        if stat.S_ISLNK(metadata.st_mode):
            _fail(f"Journal path must not contain symlinks: {current}")


def _secure_directory(path: Path, *, create: bool = False) -> None:
    if create and not path.exists():
        parent = path.parent
        _reject_symlink_components(parent, allow_missing_leaf=False)
        path.mkdir(mode=0o700)
    _reject_symlink_components(path, allow_missing_leaf=False)
    metadata = path.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        _fail(f"Journal path is not a real directory: {path}")
    if metadata.st_uid != os.geteuid():
        _fail(f"Journal directory is not owned by the invoking user: {path}")
    if stat.S_IMODE(metadata.st_mode) != 0o700:
        _fail(f"Journal directory must have mode 0700: {path}")


def _make_directories_without_links(path: Path) -> None:
    absolute = path.absolute()
    current = Path(absolute.parts[0])
    for part in absolute.parts[1:]:
        current /= part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            current.mkdir(mode=0o700)
            metadata = current.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            _fail(f"Journal path component is not a real directory: {current}")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _failpoint(name: str) -> None:
    """Terminate without cleanup at deterministic test-only transition points."""
    if os.environ.get(TEST_FAILPOINT_ENV) == name:
        os._exit(97)


def _validate_text(value: str, *, label: str, pattern: re.Pattern[str]) -> str:
    if not pattern.fullmatch(value):
        _fail(f"Journal {label} is invalid")
    return value


def _validate_document(document: Any) -> dict[str, Any]:
    if not isinstance(document, dict) or document.get("version") != JOURNAL_VERSION:
        _fail("Recovery journal has an unsupported envelope")
    required = {
        "operation_id",
        "started_at",
        "updated_at",
        "project",
        "database",
        "archive_sha256",
        "target_config_sha256",
        "target_deployment_identity",
        "phase",
        "status",
        "outcome",
        "rollback_database",
        "recovery_role",
        "original_database_oid",
        "replacement_database_oid",
        "original_role_can_login",
        "original_database_allow_connections",
        "error_code",
    }
    if set(document) != required | {"version"}:
        _fail("Recovery journal contains missing or unsupported fields")
    _validate_text(str(document["operation_id"]), label="operation ID", pattern=_UUID)
    for key in ("started_at", "updated_at"):
        value = document[key]
        if (
            not isinstance(value, str)
            or len(value) > 40
            or any(c in value for c in "\r\n\0")
        ):
            _fail(f"Journal {key} is invalid")
    _validate_text(str(document["project"]), label="project", pattern=_SAFE_NAME)
    database = document["database"]
    if (
        not isinstance(database, str)
        or not database
        or len(database) > 127
        or any(character in database for character in "\r\n\0")
    ):
        _fail("Journal database is invalid")
    for key in ("archive_sha256", "target_config_sha256", "target_deployment_identity"):
        _validate_text(str(document[key]), label=key, pattern=_HEX_64)
    _validate_text(str(document["phase"]), label="phase", pattern=_PHASE)
    if document["status"] not in _STATUS or document["outcome"] not in _OUTCOME:
        _fail("Recovery journal status or outcome is invalid")
    for key in ("rollback_database", "recovery_role"):
        value = document[key]
        if value and (not isinstance(value, str) or not _SAFE_NAME.fullmatch(value)):
            _fail(f"Journal {key} is invalid")
    for key in ("original_database_oid", "replacement_database_oid"):
        value = document[key]
        if value and (not isinstance(value, str) or not _OID.fullmatch(value)):
            _fail(f"Journal {key} is invalid")
    for key in ("original_role_can_login", "original_database_allow_connections"):
        if document[key] not in {"true", "false"}:
            _fail(f"Journal {key} is invalid")
    error_code = document["error_code"]
    if error_code and (
        not isinstance(error_code, str)
        or len(error_code) > 64
        or not re.fullmatch(r"^[A-Z0-9_]+$", error_code)
    ):
        _fail("Journal error code is invalid")
    return document


def _read(path: Path) -> dict[str, Any]:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as error:
        _fail(f"Unable to open recovery journal without following links: {error}")
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid():
            _fail("Recovery journal must be an invoking-user-owned regular file")
        if (
            stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size > MAX_JOURNAL_BYTES
        ):
            _fail("Recovery journal permissions or size are unsafe")
        with os.fdopen(descriptor, "r", encoding="utf-8", closefd=True) as source:
            descriptor = -1
            document = json.load(source)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        _fail(f"Recovery journal is not valid JSON: {error}")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return _validate_document(document)


def _write_atomic(
    directory: Path, document: dict[str, Any], *, initial: bool = False
) -> None:
    _validate_document(document)
    target = directory / JOURNAL_FILENAME
    temporary = directory / f".{JOURNAL_FILENAME}.{os.getpid()}.{secrets.token_hex(6)}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=True) as output:
            payload = json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        if initial:
            _failpoint("init_after_journal_file_fsync")
        if initial and (target.exists() or target.is_symlink()):
            _fail("An active recovery journal already exists")
        os.replace(temporary, target)
        if initial:
            _failpoint("init_after_journal_replace")
        os.chmod(target, 0o600, follow_symlinks=False)
        _fsync_directory(directory)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _active(root: Path) -> Path:
    return root / ACTIVE_DIRECTORY


def _init_directory(root: Path) -> Path:
    return root / (
        f"{INIT_DIRECTORY_PREFIX}{os.getpid()}.{secrets.token_hex(6)}"
        f"{INIT_DIRECTORY_SUFFIX}"
    )


def _archive_directory(root: Path, operation_id: str) -> Path:
    return root / (
        f"{ARCHIVE_DIRECTORY_PREFIX}{operation_id}{ARCHIVE_DIRECTORY_SUFFIX}"
    )


def _history_directory(root: Path, *, create: bool) -> Path:
    history = root / HISTORY_DIRECTORY
    if create and not history.exists():
        history.mkdir(mode=0o700)
        _failpoint("archive_after_history_directory")
        _fsync_directory(root)
        _failpoint("archive_after_history_directory_fsync")
    _secure_directory(history)
    return history


def _history_target(root: Path, operation_id: str, *, create: bool) -> Path:
    return _history_directory(root, create=create) / f"{operation_id}.json"


def _archive_receipt(root: Path) -> Path:
    return root / ARCHIVE_RECEIPT_FILENAME


def _remove_private_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            _fail(f"Recovery transition file is unsafe: {path}")
    finally:
        os.close(descriptor)
    path.unlink()


def _remove_abandoned_init_directory(root: Path, path: Path) -> None:
    _secure_directory(path)
    for child in path.iterdir():
        if child.name != JOURNAL_FILENAME and not child.name.startswith(
            f".{JOURNAL_FILENAME}."
        ):
            _fail(f"Abandoned journal publication contains an unknown entry: {child}")
        _remove_private_file(child)
    _fsync_directory(path)
    path.rmdir()
    _fsync_directory(root)


def _recover_abandoned_publications(root: Path) -> None:
    for path in sorted(root.iterdir()):
        if (
            path.name.startswith(INIT_DIRECTORY_PREFIX)
            and path.name.endswith(INIT_DIRECTORY_SUFFIX)
        ):
            _remove_abandoned_init_directory(root, path)


def _recover_abandoned_receipt_temporaries(root: Path) -> None:
    removed = False
    for path in sorted(root.iterdir()):
        if path.name.startswith(ARCHIVE_RECEIPT_TEMPORARY_PREFIX):
            _remove_private_file(path)
            removed = True
    if removed:
        _fsync_directory(root)


def _recover_legacy_empty_active(root: Path) -> None:
    active = _active(root)
    if not active.exists() or active.is_symlink():
        return
    _secure_directory(active)
    journal = active / JOURNAL_FILENAME
    if journal.exists() or journal.is_symlink():
        return
    for child in active.iterdir():
        if not child.name.startswith(f".{JOURNAL_FILENAME}."):
            _fail(f"Incomplete active journal contains an unknown entry: {child}")
        _remove_private_file(child)
    _fsync_directory(active)
    active.rmdir()
    _fsync_directory(root)


def _recover_active_temporaries(root: Path) -> None:
    active = _active(root)
    if not active.exists() or active.is_symlink():
        return
    _secure_directory(active)
    journal = active / JOURNAL_FILENAME
    if not journal.exists() and not journal.is_symlink():
        return
    _read(journal)
    removed = False
    for child in active.iterdir():
        if child == journal:
            continue
        if not child.name.startswith(f".{JOURNAL_FILENAME}."):
            _fail(f"Active recovery journal contains an unknown entry: {child}")
        _remove_private_file(child)
        removed = True
    if removed:
        _fsync_directory(active)


def _archive_operation_id(path: Path) -> str:
    name = path.name
    if not name.startswith(ARCHIVE_DIRECTORY_PREFIX) or not name.endswith(
        ARCHIVE_DIRECTORY_SUFFIX
    ):
        _fail(f"Recovery archive transition has an invalid name: {path}")
    operation_id = name[
        len(ARCHIVE_DIRECTORY_PREFIX) : -len(ARCHIVE_DIRECTORY_SUFFIX)
    ]
    return _validate_text(operation_id, label="operation ID", pattern=_UUID)


def _documents_match(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return json.dumps(left, sort_keys=True, separators=(",", ":")) == json.dumps(
        right, sort_keys=True, separators=(",", ":")
    )


def _read_archive_receipt(root: Path) -> tuple[Path, dict[str, Any]] | None:
    receipt = _archive_receipt(root)
    if not receipt.exists() and not receipt.is_symlink():
        return None
    document = _read(receipt)
    if document["status"] not in {"succeeded", "failed"}:
        _fail("Archived recovery receipt is not terminal")
    target = _history_target(root, document["operation_id"], create=False)
    if not _documents_match(document, _read(target)):
        _fail("Archived recovery receipt does not match immutable history")
    return target, document


def _publish_archive_receipt(
    root: Path, target: Path, document: dict[str, Any]
) -> None:
    existing = _read_archive_receipt(root)
    if existing is not None and _documents_match(existing[1], document):
        return
    temporary = root / f".{ARCHIVE_RECEIPT_FILENAME}.{os.getpid()}.{secrets.token_hex(6)}"
    try:
        os.link(target, temporary, follow_symlinks=False)
        _failpoint("archive_after_receipt_link")
        os.replace(temporary, _archive_receipt(root))
        _failpoint("archive_after_receipt_replace")
        _fsync_directory(root)
        _failpoint("archive_after_receipt_fsync")
    finally:
        temporary.unlink(missing_ok=True)


def _clear_archive_receipt(root: Path) -> None:
    receipt = _read_archive_receipt(root)
    if receipt is None:
        return
    _remove_private_file(_archive_receipt(root))
    _fsync_directory(root)


def _finish_archive_transition(root: Path, pending: Path) -> Path:
    operation_id = _archive_operation_id(pending)
    _secure_directory(pending)
    target = _history_target(root, operation_id, create=False)
    target_document = _read(target)
    if target_document["status"] not in {"succeeded", "failed"}:
        _fail("Published recovery journal history is not terminal")
    _publish_archive_receipt(root, target, target_document)
    source = pending / JOURNAL_FILENAME
    if source.exists() or source.is_symlink():
        source_document = _read(source)
        if source_document["operation_id"] != operation_id or not _documents_match(
            source_document, target_document
        ):
            _fail("Pending and published recovery journals do not match")
        _remove_private_file(source)
        _failpoint("archive_after_journal_unlink")
        _fsync_directory(pending)
        _failpoint("archive_after_pending_fsync")
    if any(pending.iterdir()):
        _fail(f"Recovery archive transition contains unknown entries: {pending}")
    pending.rmdir()
    _failpoint("archive_after_pending_rmdir")
    _fsync_directory(root)
    return target


def _recover_archive_transitions(root: Path) -> list[Path]:
    recovered: list[Path] = []
    for path in sorted(root.iterdir()):
        if (
            path.name.startswith(ARCHIVE_DIRECTORY_PREFIX)
            and path.name.endswith(ARCHIVE_DIRECTORY_SUFFIX)
        ):
            recovered.append(_finish_archive_transition(root, path))
    return recovered


def _recover_transitions(root: Path) -> list[Path]:
    _recover_abandoned_publications(root)
    _recover_abandoned_receipt_temporaries(root)
    _recover_legacy_empty_active(root)
    _recover_active_temporaries(root)
    return _recover_archive_transitions(root)


def _command_init(args: argparse.Namespace) -> None:
    root = Path(args.root)
    if not root.exists():
        _make_directories_without_links(root)
    _secure_directory(root)
    _recover_transitions(root)
    active = _active(root)
    if active.exists() or active.is_symlink():
        _fail(f"An unfinished recovery journal already exists at {active}")
    _clear_archive_receipt(root)
    publication = _init_directory(root)
    publication.mkdir(mode=0o700)
    _failpoint("init_after_publication_directory")
    try:
        document = {
            "version": JOURNAL_VERSION,
            "operation_id": args.operation_id,
            "started_at": args.started_at,
            "updated_at": args.started_at,
            "project": args.project,
            "database": args.database,
            "archive_sha256": args.archive_sha256,
            "target_config_sha256": args.target_config_sha256,
            "target_deployment_identity": args.target_deployment_identity,
            "phase": "prepared_before_fence",
            "status": "running",
            "outcome": "pending",
            "rollback_database": args.rollback_database,
            "recovery_role": args.recovery_role,
            "original_database_oid": args.original_database_oid,
            "replacement_database_oid": "",
            "original_role_can_login": args.original_role_can_login,
            "original_database_allow_connections": args.original_database_allow_connections,
            "error_code": "",
        }
        _write_atomic(publication, document, initial=True)
        _failpoint("init_after_journal_fsync")
        os.rename(publication, active)
        _failpoint("init_after_active_rename")
        _fsync_directory(root)
    except BaseException:
        try:
            _remove_abandoned_init_directory(root, publication)
        except (JournalError, OSError):
            pass
        raise
    print(active / JOURNAL_FILENAME)


def _command_prepare(args: argparse.Namespace) -> None:
    root = Path(args.root)
    if not root.exists():
        _make_directories_without_links(root)
    _secure_directory(root)
    lock_path = root / "operation.lock"
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as error:
        _fail(f"Unable to open the recovery operation lock safely: {error}")
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            _fail(
                "Recovery operation lock must be an invoking-user-owned mode-0600 file"
            )
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(root)
    print(lock_path)


def _command_update(args: argparse.Namespace) -> None:
    root = Path(args.root)
    _secure_directory(root)
    active = _active(root)
    _secure_directory(active)
    document = _read(active / JOURNAL_FILENAME)
    if args.expected_phase and document["phase"] != args.expected_phase:
        _fail(
            f"Recovery journal phase changed: expected {args.expected_phase}, "
            f"found {document['phase']}"
        )
    for key in (
        "phase",
        "status",
        "outcome",
        "replacement_database_oid",
        "error_code",
    ):
        value = getattr(args, key)
        if value is not None:
            document[key] = value
    document["updated_at"] = args.updated_at
    _write_atomic(active, document)


def _command_inspect(args: argparse.Namespace) -> None:
    root = Path(args.root)
    _secure_directory(root)
    recovered = _recover_transitions(root)
    active = _active(root)
    if active.exists() or active.is_symlink():
        _secure_directory(active)
        document = _read(active / JOURNAL_FILENAME)
    elif len(recovered) == 1:
        document = _read(recovered[0])
    elif recovered:
        _fail("Multiple completed archive transitions require operator inspection")
    else:
        receipt = _read_archive_receipt(root)
        if receipt is None:
            _fail("No active recovery journal exists")
        document = receipt[1]
    fields = (
        "operation_id",
        "started_at",
        "project",
        "database",
        "archive_sha256",
        "target_config_sha256",
        "target_deployment_identity",
        "phase",
        "status",
        "outcome",
        "rollback_database",
        "recovery_role",
        "original_database_oid",
        "replacement_database_oid",
        "original_role_can_login",
        "original_database_allow_connections",
        "error_code",
    )
    for field in fields:
        print(document[field])


def _command_archive(args: argparse.Namespace) -> None:
    root = Path(args.root)
    _secure_directory(root)
    recovered = _recover_transitions(root)
    active = _active(root)
    if not active.exists() and not active.is_symlink():
        if recovered:
            print(recovered[-1])
        else:
            receipt = _read_archive_receipt(root)
            if receipt is None:
                print("ARCHIVE_STATUS=already_complete")
            else:
                print(receipt[0])
        return
    _secure_directory(active)
    source = active / JOURNAL_FILENAME
    document = _read(source)
    if document["status"] not in {"succeeded", "failed"}:
        _fail("Only terminal recovery journals can be archived")
    history = _history_directory(root, create=True)
    target = history / f"{document['operation_id']}.json"
    if target.exists() or target.is_symlink():
        target_document = _read(target)
        if not _documents_match(document, target_document):
            _fail("Recovery journal history contains conflicting operation evidence")
    else:
        try:
            os.link(source, target, follow_symlinks=False)
        except OSError as error:
            _fail(f"Unable to publish recovery journal history atomically: {error}")
    _failpoint("archive_after_history_link")
    _fsync_directory(history)
    _failpoint("archive_after_history_fsync")
    _publish_archive_receipt(root, target, document)
    pending = _archive_directory(root, document["operation_id"])
    if pending.exists() or pending.is_symlink():
        _fail("Recovery journal has a conflicting archive transition")
    os.rename(active, pending)
    _failpoint("archive_after_active_rename")
    _fsync_directory(root)
    _failpoint("archive_after_active_rename_fsync")
    _finish_archive_transition(root, pending)
    print(target)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    prepare = commands.add_parser("prepare")
    prepare.add_argument("--root", required=True)
    prepare.set_defaults(handler=_command_prepare)

    init = commands.add_parser("init")
    init.add_argument("--root", required=True)
    init.add_argument("--operation-id", required=True)
    init.add_argument("--started-at", required=True)
    init.add_argument("--project", required=True)
    init.add_argument("--database", required=True)
    init.add_argument("--archive-sha256", required=True)
    init.add_argument("--target-config-sha256", required=True)
    init.add_argument("--target-deployment-identity", required=True)
    init.add_argument("--rollback-database", required=True)
    init.add_argument("--recovery-role", required=True)
    init.add_argument("--original-database-oid", required=True)
    init.add_argument(
        "--original-role-can-login", required=True, choices=("true", "false")
    )
    init.add_argument(
        "--original-database-allow-connections",
        required=True,
        choices=("true", "false"),
    )
    init.set_defaults(handler=_command_init)

    update = commands.add_parser("update")
    update.add_argument("--root", required=True)
    update.add_argument("--updated-at", required=True)
    update.add_argument("--expected-phase")
    update.add_argument("--phase")
    update.add_argument("--status", choices=sorted(_STATUS))
    update.add_argument("--outcome", choices=sorted(_OUTCOME))
    update.add_argument("--replacement-database-oid")
    update.add_argument("--error-code")
    update.set_defaults(handler=_command_update)

    inspect = commands.add_parser("inspect")
    inspect.add_argument("--root", required=True)
    inspect.set_defaults(handler=_command_inspect)

    archive = commands.add_parser("archive")
    archive.add_argument("--root", required=True)
    archive.set_defaults(handler=_command_archive)
    return parser


def main() -> int:
    try:
        args = _parser().parse_args()
        args.handler(args)
    except JournalError as error:
        print(f"ERROR [recovery_journal]: {error}", file=sys.stderr)
        return 4
    except OSError as error:
        print(
            f"ERROR [recovery_journal]: Host filesystem transition failed: {error}",
            file=sys.stderr,
        )
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
