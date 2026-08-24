#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: ./scripts/set-version.sh X.Y.Z

Update the checked-in ThreatLens application version before creating a vX.Y.Z release tag.
USAGE
}

version="${1:-}"
if [ -z "$version" ] || [ "$#" -ne 1 ]; then
  usage >&2
  exit 2
fi

if [[ "$version" == v* ]]; then
  echo "Pass the application version without the release tag prefix, for example 1.0.0." >&2
  exit 2
fi

if ! [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Version must use X.Y.Z format." >&2
  exit 2
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required to update package metadata." >&2
  exit 1
fi

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"

printf '%s\n' "$version" > "$repo_root/VERSION"

python3 - "$repo_root" "$version" <<'PY'
import json
import re
import sys
from pathlib import Path


repo_root = Path(sys.argv[1])
version = sys.argv[2]


def read_text(relative_path: str) -> str:
    return (repo_root / relative_path).read_text(encoding="utf-8")


def write_text(relative_path: str, value: str) -> None:
    (repo_root / relative_path).write_text(value, encoding="utf-8")


def update_json(relative_path: str, updater) -> None:
    path = repo_root / relative_path
    document = json.loads(path.read_text(encoding="utf-8"))
    updater(document)
    path.write_text(f"{json.dumps(document, indent=2)}\n", encoding="utf-8")


update_json("web/package.json", lambda document: document.__setitem__("version", version))


def update_package_lock(document):
    document["version"] = version
    packages = document.get("packages")
    if isinstance(packages, dict) and isinstance(packages.get(""), dict):
        packages[""]["version"] = version


update_json("web/package-lock.json", update_package_lock)

write_text(
    "backend/app/version.py",
    re.sub(r'_DEFAULT_VERSION = "[^"]+"', f'_DEFAULT_VERSION = "{version}"', read_text("backend/app/version.py")),
)

for dockerfile in ("backend/Dockerfile", "web/Dockerfile"):
    write_text(dockerfile, re.sub(r"ARG APP_VERSION=[^\n]+", f"ARG APP_VERSION={version}", read_text(dockerfile)))

write_text(
    "docker-compose.build.yml",
    re.sub(
        r"APP_VERSION: \$\{APP_VERSION:-[^}]+}",
        f"APP_VERSION: ${{APP_VERSION:-{version}}}",
        read_text("docker-compose.build.yml"),
    ),
)

PY
