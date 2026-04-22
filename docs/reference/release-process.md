# Release Process

ThreatLens treats the checked-in API, dependency, and governance artifacts as part of the shipped release contract.

## Public Release Gates

Before publishing a public tag, image, or source release:

1. Replace any `.invalid` contact placeholders in `README.md`, `SECURITY.md`, `CONTRIBUTING.md`, and `CODE_OF_CONDUCT.md` with real monitored contact paths, or explicitly enable GitHub private vulnerability reporting for security reports.
2. Regenerate the API and dependency artifacts described below.
3. Update `CHANGELOG.md` by moving relevant entries from `Unreleased` into a dated release section.
4. Verify that bundled license texts and `THIRD_PARTY_NOTICES.md` still match the shipped runtime stack and assets.

If those gates are not met yet, treat the repository as preview code rather than a public release.

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
BACKEND_IMAGE=$(docker build -q -f backend/Dockerfile backend)
docker run --rm -v "$PWD":/src -w /src "$BACKEND_IMAGE" \
  python backend/scripts/generate_dependency_inventory.py \
  --backend-output docs/reference/backend-runtime-dependencies.txt \
  --frontend-output docs/reference/frontend-runtime-dependencies.txt
```

That command intentionally runs inside the built backend image so the backend inventory matches the redistributed runtime environment rather than a development-only venv.

## Files to Review Before Release

- `CHANGELOG.md`
- `README.md`
- `THIRD_PARTY_NOTICES.md`
- `docs/reference/api.md`
- `docs/reference/openapi.json`
- `docs/reference/backend-runtime-dependencies.txt`
- `docs/reference/frontend-runtime-dependencies.txt`
- `docs/licenses/`
- `SECURITY.md`
- `CONTRIBUTING.md`
- `CODE_OF_CONDUCT.md`

## Packaged Backend Image Metadata

Built backend images write release-compliance metadata to:

- `/usr/share/doc/threatlens/backend-requirements.txt`
- `/usr/share/doc/threatlens/backend-runtime-dependencies.txt`
