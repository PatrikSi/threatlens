#!/usr/bin/env python3
"""Check reference inventories against the actual native release image pair."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import subprocess
import tempfile
import uuid


ARTIFACTS = {
    "backend": (
        "backend-runtime-dependencies.txt",
        "backend-runtime-package-metadata.json",
        "backend-runtime-package-legal",
        "backend-os-packages.txt",
        "backend-os-package-legal",
    ),
    "web": (
        "frontend-runtime-dependencies.txt",
        "frontend-runtime-package-metadata.json",
        "frontend-runtime-package-legal",
        "frontend-os-packages.txt",
        "frontend-os-package-metadata.tsv",
        "frontend-os-package-legal",
    ),
}


def file_hashes(path: Path) -> dict[str, str]:
    if not path.exists():
        raise ValueError(f"Required dependency artifact is missing: {path}")
    files = [path] if path.is_file() else sorted(path.rglob("*"))
    return {
        str(file.relative_to(path.parent)): hashlib.sha256(file.read_bytes()).hexdigest()
        for file in files
        if file.is_file()
    }


def verify_image(kind: str, image: str, reference: Path) -> list[str]:
    # Never start the application or inspect its environment. A unique stopped
    # container provides the files and remains identifiable if creation times out.
    container = f"threatlens-artifacts-{uuid.uuid4().hex}"
    mismatches: list[str] = []
    try:
        subprocess.run(
            ["docker", "create", "--name", container, "--network", "none",
             "--read-only", "--entrypoint", "/bin/true", image],
            check=True, stdout=subprocess.DEVNULL, timeout=60,
        )
        with tempfile.TemporaryDirectory(prefix="threatlens-artifacts-") as directory:
            for name in ARTIFACTS[kind]:
                target = Path(directory) / name
                subprocess.run(
                    ["docker", "cp", f"{container}:/usr/share/doc/threatlens/{name}", str(target)],
                    check=True, timeout=60,
                )
                expected, actual = file_hashes(reference / name), file_hashes(target)
                mismatches.extend(
                    path for path in sorted(expected.keys() | actual.keys())
                    if expected.get(path) != actual.get(path)
                )
    finally:
        subprocess.run(
            ["docker", "rm", "--force", container],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30,
        )
    return mismatches


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-image", required=True)
    parser.add_argument("--web-image", required=True)
    parser.add_argument("--reference-dir", type=Path, default=Path("docs/reference"))
    args = parser.parse_args()
    try:
        mismatches = []
        for kind, image in (("backend", args.backend_image), ("web", args.web_image)):
            mismatches.extend(verify_image(kind, image, args.reference_dir))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(2, f"Dependency artifact verification could not complete: {error}\n")
    if mismatches:
        print(f"Dependency artifacts differ from the built images ({len(mismatches)} files):")
        for path in mismatches[:30]:
            print(f"  {path}")
        if len(mismatches) > 30:
            print(f"  ... and {len(mismatches) - 30} more")
        return 1
    print("Dependency inventories and bundled legal files match the built image pair.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
