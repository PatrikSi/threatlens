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
- `docs/reference/frontend-os-package-legal/`

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

The backend and frontend runtime inventories, metadata inventories, and legal bundles are intentionally copied from the built container images rather than from local development environments so they reflect the redistributed Python, npm, Debian, and Alpine layers shipped by the repository Dockerfiles. ThreatLens still does not publish external supply-chain attestations, but the checked-in notice set covers both the application dependency layers and the redistributed OS package layers shipped by the repository Dockerfiles.

Bundled license texts shipped in this repository:

- `LICENSE` (Apache-2.0)
- `docs/licenses/MIT.txt`
- `docs/licenses/BSD-2-Clause.txt`
- `docs/licenses/BSD-3-Clause.txt`
- `docs/licenses/ISC.txt`
- `docs/licenses/MPL-1.1.txt`
- `docs/licenses/MPL-2.0.txt`
- `docs/licenses/Unlicense.txt`
- `docs/licenses/OFL-1.1.txt`
- `docs/licenses/GPL-2.0.txt`
- `docs/licenses/LGPL-2.1.txt`
- `docs/licenses/LGPL-3.0.txt`
- `docs/licenses/GPL-3.0.txt`
- `docs/licenses/Apache-2.0.txt`

The shared license-family texts above are convenience references for the application dependency stack and committed assets. Package-specific frontend redistribution files are committed under `docs/reference/frontend-runtime-package-legal/`, `docs/reference/frontend-os-package-legal/`, `docs/reference/backend-runtime-package-legal/`, and `docs/reference/backend-os-package-legal/`. The machine-readable metadata inventories map the copied runtime legal files harvested from installed distributions when they are published by upstream packages.

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
- Built web images ship `LICENSE`, `README.md`, `THIRD_PARTY_NOTICES.md`, the bundled license texts, `frontend-package-lock.json`, `frontend-runtime-dependencies.txt`, `frontend-runtime-package-metadata.json`, `frontend-runtime-package-legal/`, `frontend-os-packages.txt`, `frontend-os-package-metadata.tsv`, and `frontend-os-package-legal/` under `/usr/share/doc/threatlens/`.
- Built web images copy package-published `LICENSE`, `NOTICE`, `COPYING`, and similar top-level legal files from each installed frontend runtime dependency into `/usr/share/doc/threatlens/frontend-runtime-package-legal/`.
- Built web images preserve the Alpine runtime layer's per-package `APK-INFO` records under `/usr/share/doc/threatlens/frontend-os-package-legal/` and also copy any `/usr/share/licenses/<package>/` trees that the base image publishes.
- The shipped backend image is based on the pinned `python:3.12.11-slim-bookworm` image and also redistributes Debian Bookworm packages installed during the Docker build. The shipped web image is based on the pinned `nginx:1.27-alpine` image. The pinned `node:22.20.0-alpine` image is a build-stage-only input and is not redistributed in the final web image.
- The committed runtime inventories and legal bundles describe the Python and npm application dependency layers plus copied package-published legal files where available. The committed backend/frontend OS package artifacts document the Debian and Alpine layers redistributed by the repository Dockerfiles.
- Apache-2.0 third-party components use the standard Apache 2.0 license text already shipped as the repository `LICENSE`.
- The backend dependency spec installs `psycopg[binary]`, which pulls in both `psycopg` and `psycopg-binary`. ThreatLens fulfills its own redistribution posture by shipping the pinned `backend-requirements-lock.txt`, the resolved backend package metadata under `docs/reference/backend-runtime-package-metadata.json`, and the copied package-published legal files under `/usr/share/doc/threatlens/backend-runtime-package-legal/` in the built backend image. If your compliance program requires a locally linked PostgreSQL client or a different LGPL fulfillment path, rebuild from `backend/requirements.txt` with non-binary `psycopg` before redistribution.
- ThreatLens redistributes `psycopg-binary` as received from PyPI at the version pinned in `backend/requirements-lock.txt`. The release metadata identifies the exact redistributed version and upstream project/source URLs for that package family so operators and downstream redistributors can mirror the preferred-form source alongside the shipped image artifacts when their distribution model requires it.
- If you redistribute ThreatLens images or other packaged artifacts, preserve this notice file and comply with the licenses of bundled and transitive dependencies.
