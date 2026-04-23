#!/usr/bin/env python3
"""Generate runtime dependency inventories for release artifacts.

Backend inventory is generated from the currently running Python environment.
For release artifacts, run this script inside the built backend image so the
output matches the redistributed runtime stack from backend/Dockerfile.

Frontend inventory is generated from web/package-lock.json and includes only
runtime packages (entries where package-lock marks "dev" as false or absent).
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path
from pathlib import PurePosixPath
import shutil
import subprocess


_LICENSE_VALUE_NORMALIZATION = {
    "Apache 2.0": "Apache-2.0",
    "Apache License 2.0": "Apache-2.0",
    "Apache License, Version 2.0": "Apache-2.0",
    "MIT License": "MIT",
    "OSI Approved :: Apache Software License": "Apache-2.0",
    "OSI Approved :: MIT License": "MIT",
    "The BSD 2-Clause License": "BSD-2-Clause",
}


def _sorted_backend_distributions() -> list[str]:
    rows: dict[str, str] = {}
    for dist in metadata.distributions():
        name = (dist.metadata.get("Name") or "").strip()
        version = (dist.version or "").strip()
        if not name or not version:
            continue
        key = name.lower().replace("_", "-")
        rows[key] = f"{name}=={version}"
    return [rows[key] for key in sorted(rows)]


def _sorted_backend_os_packages() -> list[str]:
    result = subprocess.run(
        ["dpkg-query", "-W", "-f=${Package}=${Version}\n"],
        check=True,
        capture_output=True,
        text=True,
    )
    return sorted((line.strip() for line in result.stdout.splitlines() if line.strip()), key=str.lower)


def _classify_legal_file(path_value: str) -> str:
    lower = PurePosixPath(path_value).name.lower()
    if "license" in lower or "licence" in lower:
        return "license"
    if "notice" in lower:
        return "notice"
    if "copying" in lower:
        return "copying"
    if "authors" in lower:
        return "authors"
    if "copyright" in lower:
        return "copyright"
    return "other"


def _copy_backend_legal_files(
    dist: metadata.Distribution,
    *,
    package_name: str,
    package_version: str,
    legal_output_dir: Path | None,
) -> list[dict[str, str]]:
    copied: list[dict[str, str]] = []
    package_files = sorted((str(file) for file in dist.files or []), key=str.lower)

    for package_file in package_files:
        if _classify_legal_file(package_file) == "other":
            continue

        source_path = Path(dist.locate_file(package_file))
        if not source_path.is_file():
            continue

        contents = source_path.read_bytes()
        artifact_path = PurePosixPath(package_name, package_version, package_file).as_posix()
        if legal_output_dir is not None:
            target_path = legal_output_dir / artifact_path
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_bytes(contents)

        copied.append(
            {
                "kind": _classify_legal_file(package_file),
                "file_name": source_path.name,
                "source_path": PurePosixPath(package_file).as_posix(),
                "artifact_path": artifact_path,
                "sha256": hashlib.sha256(contents).hexdigest(),
            }
        )

    return copied


def _copy_backend_os_legal_files(legal_output_dir: Path | None) -> list[dict[str, str]]:
    copied: list[dict[str, str]] = []
    copyright_paths = sorted(Path("/usr/share/doc").glob("*/copyright"), key=lambda path: path.as_posix().lower())

    for source_path in copyright_paths:
        if not source_path.is_file():
            continue

        package_name = source_path.parent.name
        contents = source_path.read_bytes()
        artifact_path = PurePosixPath(package_name, source_path.name).as_posix()
        if legal_output_dir is not None:
            target_path = legal_output_dir / artifact_path
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_bytes(contents)

        copied.append(
            {
                "kind": "copyright",
                "file_name": source_path.name,
                "source_path": source_path.as_posix(),
                "artifact_path": artifact_path,
                "sha256": hashlib.sha256(contents).hexdigest(),
            }
        )

    return copied


def _normalize_license_value(value: str) -> str:
    normalized = value.strip()
    if not normalized or normalized.upper() == "UNKNOWN":
        return ""
    return _LICENSE_VALUE_NORMALIZATION.get(normalized, normalized)


def _resolved_backend_license(
    dist: metadata.Distribution,
    license_classifiers: list[str],
) -> tuple[str, str]:
    license_expression = _normalize_license_value((dist.metadata.get("License-Expression") or "").strip())
    if license_expression:
        return license_expression, "license_expression"

    metadata_license = _normalize_license_value((dist.metadata.get("License") or "").strip())
    if metadata_license:
        return metadata_license, "license"

    if license_classifiers:
        classifier_value = _normalize_license_value(
            license_classifiers[-1].removeprefix("License :: ").strip()
        )
        if classifier_value:
            return classifier_value, "classifier"

    return "Unknown", "unknown"


def _normalized_backend_package_metadata(legal_output_dir: Path | None = None) -> list[dict[str, object]]:
    rows: dict[str, dict[str, object]] = {}
    for dist in metadata.distributions():
        name = (dist.metadata.get("Name") or "").strip()
        version = (dist.version or "").strip()
        if not name or not version:
            continue

        key = name.lower().replace("_", "-")
        project_urls = [value.strip() for value in dist.metadata.get_all("Project-URL") or [] if value.strip()]
        license_files = [value.strip() for value in dist.metadata.get_all("License-File") or [] if value.strip()]
        license_classifiers = [
            value.strip()
            for value in dist.metadata.get_all("Classifier") or []
            if value.startswith("License :: ")
        ]
        license_value, license_source = _resolved_backend_license(dist, license_classifiers)

        row: dict[str, object] = {
            "name": name,
            "version": version,
            "license": license_value,
            "license_source": license_source,
        }

        summary = (dist.metadata.get("Summary") or "").strip()
        if summary:
            row["summary"] = summary

        home_page = (dist.metadata.get("Home-page") or "").strip()
        if home_page:
            row["home_page"] = home_page

        author = (dist.metadata.get("Author") or "").strip()
        if author:
            row["author"] = author

        metadata_license = (dist.metadata.get("License") or "").strip()
        if metadata_license:
            row["metadata_license"] = metadata_license

        license_expression = (dist.metadata.get("License-Expression") or "").strip()
        if license_expression:
            row["metadata_license_expression"] = license_expression

        redistribution_files = _copy_backend_legal_files(
            dist,
            package_name=name,
            package_version=version,
            legal_output_dir=legal_output_dir,
        )
        row["license_files"] = [file["file_name"] for file in redistribution_files if file["kind"] == "license"]
        row["notice_files"] = [file["file_name"] for file in redistribution_files if file["kind"] == "notice"]
        row["redistribution_files"] = redistribution_files

        if project_urls:
            row["project_urls"] = project_urls
        if license_classifiers:
            row["license_classifiers"] = license_classifiers
        if license_files:
            row["metadata_license_files"] = license_files
        if not redistribution_files:
            row["redistribution_note"] = (
                "No LICENSE/NOTICE/COPYING/AUTHORS-style file was published in the installed Python distribution."
            )

        rows[key] = row

    return [rows[key] for key in sorted(rows)]


def _sorted_frontend_packages(lockfile_path: Path) -> list[str]:
    data = json.loads(lockfile_path.read_text())
    packages = data.get("packages", {})
    rows: dict[str, str] = {}
    for package_path, package_meta in packages.items():
        if not package_path.startswith("node_modules/"):
            continue
        if package_meta.get("dev"):
            continue
        name = (package_meta.get("name") or package_path.split("node_modules/", 1)[1]).strip()
        version = (package_meta.get("version") or "").strip()
        if not name or not version:
            continue
        rows[name.lower()] = f"{name}=={version}"
    return [rows[key] for key in sorted(rows)]


def _write_inventory(path: Path, header: str, lines: list[str]) -> None:
    content = "\n".join(
        [
            header,
            "# Generated by backend/scripts/generate_dependency_inventory.py",
            "",
            *lines,
            "",
        ]
    )
    path.write_text(content)


def _write_json(path: Path, payload: object) -> None:
    path.write_text(f"{json.dumps(payload, indent=2, sort_keys=False)}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-output", type=Path, help="Path for the resolved backend runtime inventory.")
    parser.add_argument(
        "--backend-metadata-output",
        type=Path,
        help="Path for backend runtime package metadata JSON generated from Python package metadata.",
    )
    parser.add_argument(
        "--backend-legal-output-dir",
        type=Path,
        help="Directory for backend runtime package-published legal files copied from installed Python distributions.",
    )
    parser.add_argument(
        "--backend-os-output",
        type=Path,
        help="Path for the resolved backend OS package inventory.",
    )
    parser.add_argument(
        "--backend-os-legal-output-dir",
        type=Path,
        help="Directory for backend OS package copyright files copied from /usr/share/doc.",
    )
    parser.add_argument(
        "--frontend-output",
        type=Path,
        help="Path for the resolved frontend runtime inventory.",
    )
    parser.add_argument(
        "--frontend-lockfile",
        type=Path,
        default=Path("web/package-lock.json"),
        help="Path to the frontend package-lock.json file.",
    )
    parser.add_argument(
        "--skip-backend",
        action="store_true",
        help="Skip backend inventory generation even if --backend-output is provided.",
    )
    parser.add_argument(
        "--skip-frontend",
        action="store_true",
        help="Skip frontend inventory generation even if --frontend-output is provided.",
    )
    args = parser.parse_args()

    backend_requested = any(
        output is not None for output in (args.backend_output, args.backend_metadata_output, args.backend_legal_output_dir)
    )
    backend_metadata_payload: list[dict[str, object]] | None = None

    if backend_requested and not args.skip_backend:
        if args.backend_legal_output_dir is not None:
            shutil.rmtree(args.backend_legal_output_dir, ignore_errors=True)
            args.backend_legal_output_dir.mkdir(parents=True, exist_ok=True)
        backend_metadata_payload = _normalized_backend_package_metadata(args.backend_legal_output_dir)

    if args.backend_output and not args.skip_backend:
        args.backend_output.parent.mkdir(parents=True, exist_ok=True)
        _write_inventory(
            args.backend_output,
            "# ThreatLens backend runtime dependency inventory",
            _sorted_backend_distributions(),
        )

    if args.backend_metadata_output and not args.skip_backend:
        args.backend_metadata_output.parent.mkdir(parents=True, exist_ok=True)
        _write_json(args.backend_metadata_output, backend_metadata_payload or [])

    if args.backend_os_legal_output_dir is not None and not args.skip_backend:
        shutil.rmtree(args.backend_os_legal_output_dir, ignore_errors=True)
        args.backend_os_legal_output_dir.mkdir(parents=True, exist_ok=True)
        _copy_backend_os_legal_files(args.backend_os_legal_output_dir)

    if args.backend_os_output and not args.skip_backend:
        args.backend_os_output.parent.mkdir(parents=True, exist_ok=True)
        _write_inventory(
            args.backend_os_output,
            "# ThreatLens backend OS package inventory",
            _sorted_backend_os_packages(),
        )

    if args.frontend_output and not args.skip_frontend:
        args.frontend_output.parent.mkdir(parents=True, exist_ok=True)
        _write_inventory(
            args.frontend_output,
            "# ThreatLens frontend runtime dependency inventory",
            _sorted_frontend_packages(args.frontend_lockfile),
        )

    requested_outputs = [
        args.backend_output and not args.skip_backend,
        args.backend_metadata_output and not args.skip_backend,
        args.backend_legal_output_dir and not args.skip_backend,
        args.backend_os_output and not args.skip_backend,
        args.backend_os_legal_output_dir and not args.skip_backend,
        args.frontend_output and not args.skip_frontend,
    ]
    if not any(requested_outputs):
        parser.error(
            "No output requested. Provide --backend-output, --backend-metadata-output, --backend-legal-output-dir, and/or --frontend-output."
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
