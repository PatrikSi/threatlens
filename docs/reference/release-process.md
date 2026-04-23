# Release Process

ThreatLens treats the checked-in API, dependency, and governance artifacts as part of the shipped release contract.

## Public Release Gates

Before publishing a public tag, image, or source release:

1. Verify the public repository paths in `README.md`, `SECURITY.md`, `CONTRIBUTING.md`, and `CODE_OF_CONDUCT.md` still point to the active ThreatLens GitHub repository and maintainer profile.
2. Regenerate the API and dependency artifacts described below.
3. Copy the current OpenAPI contract anchor from `docs/reference/openapi.json` (`info.x-threatlens-contract-sha256`) into the release notes and changelog entry for the published tag.
4. Update `CHANGELOG.md` by moving relevant entries from `Unreleased` into a dated release section.
5. Verify that bundled license texts and `THIRD_PARTY_NOTICES.md` still match the shipped runtime stack and assets.
6. Refresh the image build-context mirrors under `backend/compliance/` and `web/compliance/`.

## Supported Code Lines

- Public releases should use immutable tags in `vX.Y.Z` format.
- `main` is the active public development line and receives best-effort community support.
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
BACKEND_IMAGE=$(docker build -q -f backend/Dockerfile backend)
docker run --rm -v "$PWD":/src -w /src "$BACKEND_IMAGE" sh -lc '
  rm -rf /src/docs/reference/backend-runtime-package-legal /src/docs/reference/backend-os-package-legal &&
  cp /usr/share/doc/threatlens/backend-runtime-dependencies.txt /src/docs/reference/backend-runtime-dependencies.txt &&
  cp /usr/share/doc/threatlens/backend-runtime-package-metadata.json /src/docs/reference/backend-runtime-package-metadata.json &&
  cp -R /usr/share/doc/threatlens/backend-runtime-package-legal /src/docs/reference/backend-runtime-package-legal &&
  cp /usr/share/doc/threatlens/backend-os-packages.txt /src/docs/reference/backend-os-packages.txt &&
  cp -R /usr/share/doc/threatlens/backend-os-package-legal /src/docs/reference/backend-os-package-legal'
WEB_IMAGE=$(docker build -q -f web/Dockerfile web)
docker run --rm -v "$PWD":/src -w /src "$WEB_IMAGE" sh -lc '
  rm -rf /src/docs/reference/frontend-runtime-package-legal /src/docs/reference/frontend-os-package-legal &&
  cp /usr/share/doc/threatlens/frontend-runtime-dependencies.txt /src/docs/reference/frontend-runtime-dependencies.txt &&
  cp /usr/share/doc/threatlens/frontend-runtime-package-metadata.json /src/docs/reference/frontend-runtime-package-metadata.json &&
  cp -R /usr/share/doc/threatlens/frontend-runtime-package-legal /src/docs/reference/frontend-runtime-package-legal &&
  cp /usr/share/doc/threatlens/frontend-os-packages.txt /src/docs/reference/frontend-os-packages.txt &&
  cp /usr/share/doc/threatlens/frontend-os-package-metadata.tsv /src/docs/reference/frontend-os-package-metadata.tsv &&
  cp -R /usr/share/doc/threatlens/frontend-os-package-legal /src/docs/reference/frontend-os-package-legal'
```

That sequence intentionally refreshes the checked-in backend runtime lockfile, syncs the mirrored compliance bundle used by the backend/web build contexts, builds the backend and web images, and copies the packaged compliance artifacts back into `docs/reference/`. Those artifacts cover both the application dependency layers and the redistributed OS package layers shipped by the repository Dockerfiles.

The backend image installs its Python application dependency layer from the checked-in `backend/requirements-lock.txt` file, and the frontend image resolves its application dependency layer from `web/package-lock.json`. The Dockerfiles and compose base images are pinned by digest. Application dependencies are therefore version-pinned by source control, but the backend image still installs Debian packages from the live Bookworm apt repositories, so full byte-for-byte rebuild reproducibility is not claimed yet.

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
