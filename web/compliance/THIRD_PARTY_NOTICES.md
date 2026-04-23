# Third-Party Notices

ThreatLens is licensed under Apache-2.0. This repository and its container images also redistribute third-party components. This file supplements the project `LICENSE`; it does not replace the license terms of upstream dependencies.

## Dependency Sources and Runtime Inventories

Authoritative release inputs in this repository:

- `backend/requirements.txt` for the backend direct runtime requirements
- `backend/requirements-lock.txt` for the pinned backend Python application dependency set installed by `backend/Dockerfile`
- `backend/requirements-dev.txt` for backend development and test-only extras
- `web/package-lock.json` for the exact frontend runtime resolution installed by `web/Dockerfile`
- `backend/Dockerfile` and `web/Dockerfile` for the pinned base images used to build the shipped containers

Generated resolved runtime inventories committed in this repository:

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

Refresh the backend runtime lockfile and regenerate those inventory files and legal bundles with:

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

The backend dependency inventory, backend metadata inventory, backend runtime legal bundle, and backend OS package notice set are intentionally copied from the built backend image rather than a local development venv so they reflect the redistributed Python and Debian layers installed by `backend/Dockerfile`. The frontend runtime inventory, frontend metadata inventory, and frontend legal bundle are regenerated from a clean Node container using the checked-in npm lockfile, while the frontend Alpine package inventory is regenerated from the pinned `nginx:1.27-alpine` runtime base. ThreatLens still does not publish external supply-chain attestations, but the checked-in notice set now covers both the application dependency layers and the redistributed OS package layers shipped by the repository Dockerfiles.

Bundled license texts shipped in this repository:

- `LICENSE` (Apache-2.0)
- `docs/licenses/MIT.txt`
- `docs/licenses/BSD-2-Clause.txt`
- `docs/licenses/BSD-3-Clause.txt`
- `docs/licenses/ISC.txt`
- `docs/licenses/MPL-2.0.txt`
- `docs/licenses/Unlicense.txt`
- `docs/licenses/OFL-1.1.txt`
- `docs/licenses/LGPL-3.0.txt`
- `docs/licenses/GPL-3.0.txt`

The MIT/BSD family texts above are supplemental family references. Package-specific frontend redistribution files are committed under `docs/reference/frontend-runtime-package-legal/`, and `docs/reference/frontend-runtime-package-metadata.json` maps each runtime package to the copied legal files. The repository now also commits `docs/reference/backend-runtime-package-legal/`, while `docs/reference/backend-runtime-package-metadata.json` maps the copied backend legal files harvested from installed Python distributions when they are published in the wheel metadata. Built backend images also preserve wheel-provided notice files and upstream package metadata under the installed Python `.dist-info/` directories.

## Bundled Frontend Assets

These files are committed directly in this repository:

| Component | Files | License | Notes |
|---|---|---|
| Source Sans 3 | `web/public/fonts/source-sans-3-400.ttf`, `source-sans-3-600.ttf`, `source-sans-3-700.ttf` | SIL Open Font License 1.1 | Copyright 2010-2024 Adobe; Reserved Font Name `Source` |
| Space Grotesk | `web/public/fonts/space-grotesk-500.ttf`, `space-grotesk-700.ttf` | SIL Open Font License 1.1 | Copyright 2020 The Space Grotesk Project Authors |

## Selected Direct Backend Runtime Dependencies

| Package | Version | License |
|---|---:|---|
| fastapi | 0.116.1 | MIT |
| uvicorn | 0.35.0 | BSD-3-Clause |
| SQLAlchemy | 2.0.42 | MIT |
| psycopg | 3.2.9 | GNU Lesser General Public License v3 (LGPLv3) |
| psycopg-binary | 3.2.9 | GNU Lesser General Public License v3 (LGPLv3) |
| alembic | 1.16.4 | MIT |
| pydantic-settings | 2.10.1 | MIT |
| python-jose | 3.5.0 | MIT |
| passlib | 1.7.4 | BSD |
| bcrypt | 4.0.1 | Apache-2.0 |
| celery | 5.5.3 | BSD-3-Clause |
| redis | 6.2.0 | MIT |
| httpx | 0.28.1 | BSD-3-Clause |
| feedparser | 6.0.11 | BSD-2-Clause |
| trafilatura | 2.0.0 | Apache-2.0 |
| readability-lxml | 0.8.4.1 | Apache-2.0 |
| beautifulsoup4 | 4.13.4 | MIT |
| python-dateutil | 2.9.0.post0 | Apache-2.0 and BSD-3-Clause |
| python-multipart | 0.0.20 | Apache-2.0 |
| email-validator | 2.2.0 | Unlicense |
| croniter | 3.0.3 | MIT |

## Selected Transitive Backend Runtime Dependencies

| Package | Version | License |
|---|---:|---|
| certifi | 2026.2.25 | Mozilla Public License 2.0 (MPL-2.0) |
| dnspython | 2.8.0 | ISC |

## Selected Direct Frontend Runtime Dependencies

| Package | Version | License |
|---|---:|---|
| @tanstack/react-query | 5.97.0 | MIT |
| react | 19.2.5 | MIT |
| react-dom | 19.2.5 | MIT |
| react-router-dom | 7.14.0 | MIT |

## Distribution Notes

- Built web bundles include code from the frontend runtime dependencies listed above.
- Docker images built from this repository also install transitive Python and npm dependencies resolved from the lockfiles. The full resolved runtime inventories plus the generated backend/frontend package-legal bundles are committed under `docs/reference/` and can be regenerated with the command above.
- Built backend images ship `LICENSE`, `README.md`, `THIRD_PARTY_NOTICES.md`, the bundled license texts, `backend-requirements.txt`, `backend-requirements-lock.txt`, `backend-runtime-dependencies.txt`, `backend-runtime-package-metadata.json`, `backend-runtime-package-legal/`, `backend-os-packages.txt`, and `backend-os-package-legal/` under `/usr/share/doc/threatlens/`.
- Built web images ship `LICENSE`, `README.md`, `THIRD_PARTY_NOTICES.md`, the bundled license texts, `frontend-package-lock.json`, `frontend-runtime-dependencies.txt`, `frontend-runtime-package-metadata.json`, `frontend-runtime-package-legal/`, `frontend-os-packages.txt`, and `frontend-os-package-metadata.tsv` under `/usr/share/doc/threatlens/`.
- Built web images copy package-published `LICENSE`, `NOTICE`, `COPYING`, and similar top-level legal files from each installed frontend runtime dependency into `/usr/share/doc/threatlens/frontend-runtime-package-legal/`.
- The shipped backend image is based on the pinned `python:3.12.11-slim-bookworm` image and also redistributes Debian Bookworm packages installed during the Docker build. The shipped web image is based on the pinned `nginx:1.27-alpine` image. The pinned `node:22.20.0-alpine` image is a build-stage-only input and is not redistributed in the final web image.
- The committed runtime inventories and legal bundles describe the Python and npm application dependency layers plus copied package-published legal files where available. The committed backend/frontend OS package artifacts document the Debian and Alpine layers redistributed by the repository Dockerfiles.
- Apache-2.0 third-party components use the standard Apache 2.0 license text already shipped as the repository `LICENSE`.
- The backend dependency spec installs `psycopg[binary]`, which pulls in both `psycopg` and `psycopg-binary`. Redistributors should preserve the shipped license texts, the backend lockfile, and the upstream wheel metadata already present in the image. If your compliance program requires a locally linked PostgreSQL client or a different LGPL fulfillment path, rebuild from `backend/requirements.txt` with non-binary `psycopg` before redistribution.
- ThreatLens redistributes `psycopg-binary` as received from PyPI at the version pinned in `backend/requirements-lock.txt`. Downstream redistributors should review the upstream project source/wheel publication path and satisfy any corresponding LGPL source-offer or relinkability obligations required by their distribution model.
- If you redistribute ThreatLens images or other packaged artifacts, preserve this notice file and comply with the licenses of bundled and transitive dependencies.
