# Release Process

ThreatLens treats the checked-in API, dependency, and governance artifacts as part of the shipped release contract.

## Public Release Gates

Before publishing a public tag, image, or source release:

1. Verify the public repository paths in `README.md`, `SECURITY.md`, `CONTRIBUTING.md`, and `CODE_OF_CONDUCT.md` still point to the active ThreatLens GitHub repository and maintainer profile.
2. Regenerate the API and dependency artifacts described below.
3. Update `CHANGELOG.md` by moving relevant entries from `Unreleased` into a dated release section.
4. Verify that bundled license texts and `THIRD_PARTY_NOTICES.md` still match the shipped runtime stack and assets.
5. Refresh the image build-context mirrors under `backend/compliance/` and `web/compliance/`.

## Supported Code Lines

- Public releases should use immutable tags in `vX.Y.Z` format.
- `main` is development work toward the next release and is not the stable support target.
- Only the latest published tag is considered a supported release line.

## Contract Artifact Workflow

When a change affects the published API contract:

```bash
./backend/.venv/bin/python backend/scripts/generate_api_reference.py
```

When a change affects shipped runtime dependencies, bundled assets, or redistribution guidance:

```bash
./backend/.venv/bin/python backend/scripts/generate_runtime_lockfile.py
./backend/.venv/bin/python scripts/sync_compliance_bundle.py
BACKEND_IMAGE=$(docker build -q -f backend/Dockerfile backend)
docker run --rm -v "$PWD":/src -w /src "$BACKEND_IMAGE" \
  python backend/scripts/generate_dependency_inventory.py \
  --backend-output docs/reference/backend-runtime-dependencies.txt \
  --backend-metadata-output docs/reference/backend-runtime-package-metadata.json \
  --frontend-output docs/reference/frontend-runtime-dependencies.txt
docker run --rm -v "$PWD":/src -w /src/web node:22.20.0-alpine \
  sh -lc 'npm ci >/dev/null && node ./scripts/generate_runtime_package_metadata.mjs --output /src/docs/reference/frontend-runtime-package-metadata.json'
```

That sequence intentionally refreshes the checked-in backend runtime lockfile, syncs the mirrored compliance bundle used by the backend/web build contexts, regenerates the backend runtime inventory and backend package metadata inside the built backend image, and then regenerates the frontend package metadata from an `npm ci` install in a clean container.

The backend image now installs from the checked-in `backend/requirements-lock.txt` file, and the frontend image resolves from `web/package-lock.json`. The Dockerfiles and compose base images are pinned by digest. Application dependencies are therefore version-pinned by source control, but the backend image still installs Debian packages from the live Bookworm apt repositories, so full byte-for-byte rebuild reproducibility is not claimed yet.

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
- `docs/licenses/`
- `SECURITY.md`
- `CONTRIBUTING.md`
- `CODE_OF_CONDUCT.md`

## Packaged Backend Image Metadata

Built backend images write release-compliance metadata to:

- `/usr/share/doc/threatlens/LICENSE`
- `/usr/share/doc/threatlens/THIRD_PARTY_NOTICES.md`
- `/usr/share/doc/threatlens/licenses/`
- `/usr/share/doc/threatlens/backend-requirements.txt`
- `/usr/share/doc/threatlens/backend-requirements-lock.txt`
- `/usr/share/doc/threatlens/backend-runtime-dependencies.txt`
- `/usr/share/doc/threatlens/backend-runtime-package-metadata.json`

Built web images write release-compliance metadata to:

- `/usr/share/doc/threatlens/LICENSE`
- `/usr/share/doc/threatlens/THIRD_PARTY_NOTICES.md`
- `/usr/share/doc/threatlens/licenses/`
- `/usr/share/doc/threatlens/frontend-package-lock.json`
- `/usr/share/doc/threatlens/frontend-runtime-dependencies.txt`
- `/usr/share/doc/threatlens/frontend-runtime-package-metadata.json`
