# Release Process

ThreatLens treats the checked-in API, dependency, and governance artifacts as part of the shipped release contract.

## Public Release Gates

Before publishing a public tag, image, or source release:

1. Verify the repository paths in `README.md`, `SECURITY.md`, `CONTRIBUTING.md`, and `CODE_OF_CONDUCT.md` still point to the active ThreatLens GitHub repository and still describe its actual access posture correctly.
2. Verify `SECURITY.md` and `CODE_OF_CONDUCT.md` still match the reporting channels that are actually published. Only mention a GitHub advisory URL after the repository UI exposes private vulnerability reporting and maintainers have verified it.
3. Regenerate the API and dependency artifacts described below.
4. Copy the current OpenAPI contract anchor from `docs/reference/openapi.json` (`info.x-threatlens-contract-sha256`) into the release notes and changelog entry for the published tag.
5. Run the contract-anchor guard below to confirm `CHANGELOG.md` matches the checked-in schema.
6. Update `CHANGELOG.md` by moving relevant entries from `Unreleased` into a dated release section.
7. Verify that bundled license texts and `THIRD_PARTY_NOTICES.md` still match the shipped runtime stack and assets.
8. Refresh the image build-context mirrors under `backend/compliance/` and `web/compliance/`.

If you are preparing the first open-source release, treat repository visibility and security-reporting setup as part of the release gate:

1. Make the repository public only when the reviewed release commit, images, and docs are ready.
2. Enable GitHub private vulnerability reporting for the now-public repository, or publish an equivalent repo-owned confidential vulnerability intake path, before announcing the release.
3. Update `SECURITY.md` to point to the live confidential advisory/reporting path before announcing the release.
4. Recheck `README.md`, `CONTRIBUTING.md`, and `CODE_OF_CONDUCT.md` so public community and reporting paths are accurate.

## Supported Code Lines

- Public releases should use immutable tags in `vX.Y.Z` format.
- Once the repository is public, `main` is the active public development line and receives best-effort community support.
- Only the latest published tag is considered a supported release line once tags exist.
- If no public tag exists yet, pin the deployed commit SHA, image digests, and checked-in OpenAPI contract anchor together as the current release reference.

## Contract Artifact Workflow

When a change affects the published API contract:

```bash
./backend/.venv/bin/python backend/scripts/generate_api_reference.py
```

That command refreshes both `docs/reference/api.md` and `docs/reference/openapi.json`. The generated schema now carries an immutable contract digest at `info.x-threatlens-contract-sha256`; public release notes should record that digest alongside the eventual `vX.Y.Z` tag.

When a change affects shipped runtime dependencies, bundled assets, or redistribution guidance:

```bash
./backend/.venv/bin/python backend/scripts/generate_runtime_lockfile.py
./backend/.venv/bin/python scripts/sync_compliance_bundle.py
export BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
export VCS_REF="$(git rev-parse HEAD)"
BACKEND_IMAGE=$(docker build \
  --build-arg BUILD_DATE="$BUILD_DATE" \
  --build-arg VCS_REF="$VCS_REF" \
  -q -f backend/Dockerfile backend)
docker run --rm -v "$PWD":/src -w /src "$BACKEND_IMAGE" sh -lc '
  rm -rf /src/docs/reference/backend-runtime-package-legal /src/docs/reference/backend-os-package-legal &&
  cp /usr/share/doc/threatlens/backend-runtime-dependencies.txt /src/docs/reference/backend-runtime-dependencies.txt &&
  cp /usr/share/doc/threatlens/backend-runtime-package-metadata.json /src/docs/reference/backend-runtime-package-metadata.json &&
  cp -R /usr/share/doc/threatlens/backend-runtime-package-legal /src/docs/reference/backend-runtime-package-legal &&
  cp /usr/share/doc/threatlens/backend-os-packages.txt /src/docs/reference/backend-os-packages.txt &&
  cp -R /usr/share/doc/threatlens/backend-os-package-legal /src/docs/reference/backend-os-package-legal'
WEB_IMAGE=$(docker build \
  --build-arg BUILD_DATE="$BUILD_DATE" \
  --build-arg VCS_REF="$VCS_REF" \
  -q -f web/Dockerfile web)
docker run --rm -v "$PWD":/src -w /src "$WEB_IMAGE" sh -lc '
  rm -rf /src/docs/reference/frontend-runtime-package-legal /src/docs/reference/frontend-os-package-legal &&
  cp /usr/share/doc/threatlens/frontend-runtime-dependencies.txt /src/docs/reference/frontend-runtime-dependencies.txt &&
  cp /usr/share/doc/threatlens/frontend-runtime-package-metadata.json /src/docs/reference/frontend-runtime-package-metadata.json &&
  cp -R /usr/share/doc/threatlens/frontend-runtime-package-legal /src/docs/reference/frontend-runtime-package-legal &&
  cp /usr/share/doc/threatlens/frontend-os-packages.txt /src/docs/reference/frontend-os-packages.txt &&
  cp /usr/share/doc/threatlens/frontend-os-package-metadata.tsv /src/docs/reference/frontend-os-package-metadata.tsv &&
  cp -R /usr/share/doc/threatlens/frontend-os-package-legal /src/docs/reference/frontend-os-package-legal'
```

That sequence intentionally refreshes the checked-in backend runtime lockfile, syncs the mirrored compliance bundle used by the backend/web build contexts, builds the backend and web images, and copies the packaged compliance artifacts back into `docs/reference/`. Those artifacts cover both the application dependency layers and the redistributed OS package layers shipped by the repository Dockerfiles. The standard `docker compose build` and `docker compose up --build` flow now forwards those same exported `BUILD_DATE` and `VCS_REF` values into every built ThreatLens image, so local compose builds and the explicit compliance rebuild commands carry matching OCI provenance labels.

## Contract-Anchor Guard

Use this check before tagging or merging a changelog update that references the published API contract:

```bash
python3 - <<'PY'
import json
import re
import sys
from pathlib import Path

expected = json.loads(Path("docs/reference/openapi.json").read_text())["info"]["x-threatlens-contract-sha256"]
match = re.search(
    r"Current checked-in OpenAPI contract anchor: `openapi-sha256:([0-9a-f]{64})`",
    Path("CHANGELOG.md").read_text(),
)
if not match:
    sys.exit("CHANGELOG.md is missing the current OpenAPI contract anchor entry")
actual = match.group(1)
if actual != expected:
    sys.exit(
        "CHANGELOG.md OpenAPI contract anchor mismatch: "
        f"{actual} != {expected}"
    )
print(f"CHANGELOG.md matches docs/reference/openapi.json: {expected}")
PY
```

The backend image installs its Python application dependency layer from the checked-in `backend/requirements-lock.txt` file, and the frontend image resolves its application dependency layer from `web/package-lock.json`. The Dockerfiles and compose base images are pinned by digest, and the repository Dockerfiles do not install additional live apt packages during backend or frontend builds. ThreatLens still does not claim full byte-for-byte rebuild reproducibility, because rebuilds continue to depend on external registries serving those pinned base images and lockfile-resolved application packages.

## Files to Review Before Release

- `CHANGELOG.md`
- `README.md`
- `THIRD_PARTY_NOTICES.md`
- `docs/reference/api.md`
- `docs/reference/openapi.json`
- `docs/reference/backend-runtime-dependencies.txt`
- `docs/reference/frontend-runtime-dependencies.txt`
- `docs/reference/backend-runtime-package-metadata.json`
- `docs/reference/frontend-runtime-package-metadata.json`
- `docs/reference/backend-runtime-package-legal/`
- `docs/reference/frontend-runtime-package-legal/`
- `docs/reference/backend-os-packages.txt`
- `docs/reference/backend-os-package-legal/`
- `docs/reference/frontend-os-packages.txt`
- `docs/reference/frontend-os-package-metadata.tsv`
- `docs/reference/frontend-os-package-legal/`
- `docs/licenses/`
- `SECURITY.md`
- `CONTRIBUTING.md`
- `CODE_OF_CONDUCT.md`

## Packaged Backend Image Metadata

Built backend images write release-compliance metadata to:

- `/usr/share/doc/threatlens/LICENSE`
- `/usr/share/doc/threatlens/NOTICE`
- `/usr/share/doc/threatlens/README.md`
- `/usr/share/doc/threatlens/THIRD_PARTY_NOTICES.md`
- `/usr/share/doc/threatlens/licenses/`
- `/usr/share/doc/threatlens/backend-requirements.txt`
- `/usr/share/doc/threatlens/backend-requirements-lock.txt`
- `/usr/share/doc/threatlens/backend-runtime-dependencies.txt`
- `/usr/share/doc/threatlens/backend-runtime-package-metadata.json`
- `/usr/share/doc/threatlens/backend-runtime-package-legal/`
- `/usr/share/doc/threatlens/backend-os-packages.txt`
- `/usr/share/doc/threatlens/backend-os-package-legal/`

Built web images write release-compliance metadata to:

- `/usr/share/doc/threatlens/LICENSE`
- `/usr/share/doc/threatlens/NOTICE`
- `/usr/share/doc/threatlens/README.md`
- `/usr/share/doc/threatlens/THIRD_PARTY_NOTICES.md`
- `/usr/share/doc/threatlens/licenses/`
- `/usr/share/doc/threatlens/frontend-package-lock.json`
- `/usr/share/doc/threatlens/frontend-runtime-dependencies.txt`
- `/usr/share/doc/threatlens/frontend-runtime-package-metadata.json`
- `/usr/share/doc/threatlens/frontend-runtime-package-legal/`
- `/usr/share/doc/threatlens/frontend-os-packages.txt`
- `/usr/share/doc/threatlens/frontend-os-package-metadata.tsv`
- `/usr/share/doc/threatlens/frontend-os-package-legal/`
