#!/usr/bin/env python3
"""Validate or refresh the backend runtime lockfile with pip's resolver."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

EXCLUDED = {canonicalize_name(name) for name in {"pip", "setuptools", "wheel"}}
LOCK_HEADER = (
    "# Locked backend runtime dependency set used by backend/Dockerfile.\n"
    "# Validate with generate_runtime_lockfile.py; pass --upgrade to resolve new pins.\n"
)


class LockfileError(RuntimeError):
    """Raised when requirements and the resolved runtime lock disagree."""


def _requirement_lines(requirements_path: Path) -> list[str]:
    lines: list[str] = []
    for raw_line in requirements_path.read_text(encoding="utf-8").splitlines():
        candidate = raw_line.split("#", 1)[0].strip()
        if not candidate or candidate.startswith("-"):
            continue
        lines.append(candidate)
    return lines


def _marker_matches(requirement: Requirement) -> bool:
    if requirement.marker is None:
        return True

    environment = default_environment()
    extras = set(requirement.extras)
    if not extras:
        return requirement.marker.evaluate({**environment, "extra": ""})
    return any(requirement.marker.evaluate({**environment, "extra": extra}) for extra in extras)


def _locked_requirements(lock_path: Path) -> dict[str, Requirement]:
    locked: dict[str, Requirement] = {}
    for line in _requirement_lines(lock_path):
        requirement = Requirement(line)
        key = canonicalize_name(requirement.name)
        pins = list(requirement.specifier)
        if requirement.url or len(pins) != 1 or pins[0].operator != "==" or pins[0].version.endswith(".*"):
            raise LockfileError(f"Lock entry must be one exact package pin: {line}")
        if key in locked:
            raise LockfileError(f"Lockfile contains duplicate package: {requirement.name}")
        locked[key] = requirement
    if not locked:
        raise LockfileError(f"Lockfile is empty: {lock_path}")
    return locked


def _validate_direct_requirements(requirements_path: Path, locked: dict[str, Requirement]) -> None:
    for line in _requirement_lines(requirements_path):
        requirement = Requirement(line)
        if not _marker_matches(requirement):
            continue
        key = canonicalize_name(requirement.name)
        locked_requirement = locked.get(key)
        if locked_requirement is None:
            raise LockfileError(f"Direct requirement is missing from the lockfile: {requirement}")
        locked_version = Version(next(iter(locked_requirement.specifier)).version)
        if locked_version not in requirement.specifier:
            raise LockfileError(
                f"Locked {locked_requirement} does not satisfy direct requirement {requirement}"
            )


def _resolve(
    requirements_path: Path,
    *,
    constraints_path: Path | None,
    timeout_seconds: int,
) -> dict[str, tuple[str, str]]:
    with tempfile.TemporaryDirectory(prefix="threatlens-runtime-lock-") as temp_dir:
        report_path = Path(temp_dir) / "pip-report.json"
        command = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--isolated",
            "--dry-run",
            "--ignore-installed",
            "--disable-pip-version-check",
            "--no-input",
            "--report",
            os.fspath(report_path),
            "--requirement",
            os.fspath(requirements_path),
        ]
        if constraints_path is not None:
            command.extend(["--constraint", os.fspath(constraints_path)])

        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise LockfileError(f"pip resolver exceeded {timeout_seconds} seconds") from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or "unknown resolver error"
            raise LockfileError(f"pip could not resolve the runtime requirements:\n{detail}")

        report = json.loads(report_path.read_text(encoding="utf-8"))

    resolved: dict[str, tuple[str, str]] = {}
    for distribution in report.get("install", []):
        package_metadata = distribution.get("metadata", {})
        name = str(package_metadata.get("name", "")).strip()
        version = str(package_metadata.get("version", "")).strip()
        key = canonicalize_name(name)
        if not name or not version or key in EXCLUDED:
            continue
        previous = resolved.get(key)
        if previous is not None and previous[1] != version:
            raise LockfileError(f"Resolver returned conflicting versions for {name}")
        resolved[key] = (name, version)
    if not resolved:
        raise LockfileError("pip resolver returned no runtime packages")
    return resolved


def _validate_resolved_lock(
    resolved: dict[str, tuple[str, str]],
    locked: dict[str, Requirement],
) -> None:
    resolved_versions = {key: version for key, (_, version) in resolved.items()}
    locked_versions = {
        key: next(iter(requirement.specifier)).version for key, requirement in locked.items()
    }
    if resolved_versions == locked_versions:
        return

    differences: list[str] = []
    for key in sorted(set(resolved_versions) | set(locked_versions)):
        resolved_version = resolved_versions.get(key)
        locked_version = locked_versions.get(key)
        if resolved_version == locked_version:
            continue
        differences.append(
            f"  {key}: locked={locked_version or '<missing>'}, resolved={resolved_version or '<missing>'}"
        )
    raise LockfileError(
        "Resolved runtime dependency set does not match the lockfile:\n" + "\n".join(differences)
    )


def _render_resolved_lock(resolved: dict[str, tuple[str, str]]) -> str:
    rows = [f"{name}=={version}" for _, (name, version) in sorted(resolved.items())]
    return LOCK_HEADER + "\n".join(rows) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("backend/requirements-lock.txt"),
        help="Path for the validated or refreshed backend runtime lockfile.",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("backend/requirements.txt"),
        help="Runtime requirements input used as the dependency roots.",
    )
    parser.add_argument(
        "--constraints",
        type=Path,
        default=Path("backend/requirements-lock.txt"),
        help="Existing exact lock used to constrain and verify a normal run.",
    )
    parser.add_argument(
        "--upgrade",
        action="store_true",
        help="Resolve current package versions without the existing lock constraints.",
    )
    parser.add_argument(
        "--resolver-timeout-seconds",
        type=int,
        default=300,
        help="Maximum time allowed for pip's resolver.",
    )
    args = parser.parse_args(argv)
    if args.resolver_timeout_seconds <= 0:
        parser.error("--resolver-timeout-seconds must be greater than zero")

    try:
        if args.upgrade:
            resolved = _resolve(
                args.input,
                constraints_path=None,
                timeout_seconds=args.resolver_timeout_seconds,
            )
            output = _render_resolved_lock(resolved)
        else:
            locked = _locked_requirements(args.constraints)
            _validate_direct_requirements(args.input, locked)
            resolved = _resolve(
                args.input,
                constraints_path=args.constraints,
                timeout_seconds=args.resolver_timeout_seconds,
            )
            _validate_resolved_lock(resolved, locked)
            output = args.constraints.read_text(encoding="utf-8")
    except (LockfileError, OSError, json.JSONDecodeError) as exc:
        parser.exit(1, f"runtime lockfile error: {exc}\n")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(output, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
