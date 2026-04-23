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
- `main` is development work toward the next release and is not the stable support target.
- Only the latest published tag is considered a supported release line.
- If no public tag exists yet, the repository should be treated as preview-only and the checked-in OpenAPI contract anchor is the strongest immutable in-repo reference.

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
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD":/src node:22.20.0-alpine sh -lc '
  rm -rf /tmp/web && cp -R /src/web /tmp/web && cd /tmp/web && npm ci >/dev/null &&
  node - <<'"'"'NODE'"'"' > /src/docs/reference/frontend-runtime-dependencies.txt
const fs = require("fs");
const lock = JSON.parse(fs.readFileSync("package-lock.json", "utf8"));
const packages = lock.packages || {};
const rows = [];
for (const [packagePath, packageMeta] of Object.entries(packages)) {
  if (!packagePath.startsWith("node_modules/")) continue;
  if (packageMeta.dev) continue;
  const fallbackName = packagePath.slice("node_modules/".length);
  const name = (packageMeta.name || fallbackName).trim();
  const version = (packageMeta.version || "").trim();
  if (!name || !version) continue;
  rows.push(`${name}==${version}`);
}
rows.sort((a, b) => a.localeCompare(b));
process.stdout.write([
  "# ThreatLens frontend runtime dependency inventory",
  "# Generated from web/package-lock.json in a clean Node container",
  "",
  ...rows,
  "",
].join("\\n"));
NODE
  node ./scripts/generate_runtime_package_metadata.mjs \
    --output /src/docs/reference/frontend-runtime-package-metadata.json \
    --legal-output-dir /src/docs/reference/frontend-runtime-package-legal'
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD":/src nginx:1.27-alpine sh -lc '
  {
    printf "%s\n" "# ThreatLens frontend OS package inventory" "# Generated from /lib/apk/db/installed" "";
    awk '\''BEGIN { RS=""; FS="\n" } { package=""; version=""; for (i=1; i<=NF; i++) { if ($i ~ /^P:/) package=substr($i, 3); else if ($i ~ /^V:/) version=substr($i, 3); } if (package != "" && version != "") print package "=" version; }'\'' /lib/apk/db/installed | sort;
    printf "\n";
  } > /src/docs/reference/frontend-os-packages.txt &&
  {
    printf "%s\n" "# ThreatLens frontend OS package metadata" "# Generated from /lib/apk/db/installed" "";
    printf "%s\n" "package\tversion\tlicense\torigin\thomepage";
    awk '\''BEGIN { RS=""; FS="\n" } { package=""; version=""; license=""; origin=""; homepage=""; for (i=1; i<=NF; i++) { if ($i ~ /^P:/) package=substr($i, 3); else if ($i ~ /^V:/) version=substr($i, 3); else if ($i ~ /^L:/) license=substr($i, 3); else if ($i ~ /^o:/) origin=substr($i, 3); else if ($i ~ /^U:/) homepage=substr($i, 3); } if (package != "") printf "%s\t%s\t%s\t%s\t%s\n", package, version, license, origin, homepage; }'\'' /lib/apk/db/installed | sort;
    printf "\n";
  } > /src/docs/reference/frontend-os-package-metadata.tsv'
```

That sequence intentionally refreshes the checked-in backend runtime lockfile, syncs the mirrored compliance bundle used by the backend/web build contexts, copies the backend image-packaged compliance artifacts back into `docs/reference/`, regenerates the frontend application-layer artifacts from a clean Node container, and regenerates the frontend Alpine package metadata from the pinned runtime base image. Those artifacts cover both the application dependency layers and the redistributed OS package layers shipped by the repository Dockerfiles.

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
