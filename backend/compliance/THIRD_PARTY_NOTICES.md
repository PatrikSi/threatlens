# Third-Party Notices

ThreatLens is licensed under Apache-2.0. This repository and its container images also redistribute third-party components. This file supplements the project `LICENSE`; it does not replace the license terms of upstream dependencies.

## Dependency Sources and Runtime Inventories

Authoritative release inputs in this repository:

- `backend/requirements.txt` for the backend direct runtime requirements
- `backend/requirements-lock.txt` for the exact backend runtime set installed by `backend/Dockerfile`
- `backend/requirements-dev.txt` for backend development and test-only extras
- `web/package-lock.json` for the exact frontend runtime resolution installed by `web/Dockerfile`
- `backend/Dockerfile` and `web/Dockerfile` for the pinned base images used to build the shipped containers

Generated resolved runtime inventories committed in this repository:

- `docs/reference/backend-runtime-dependencies.txt`
- `docs/reference/frontend-runtime-dependencies.txt`
- `docs/reference/backend-runtime-package-metadata.json`
- `docs/reference/frontend-runtime-package-metadata.json`

Refresh the backend runtime lockfile and regenerate those inventory files with:

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

The backend inventory and backend metadata inventory are intentionally generated from the built backend image rather than a local development venv so they reflect the redistributed runtime environment installed by `backend/Dockerfile`. The frontend inventory is generated from the committed npm lockfile, and the frontend metadata inventory is generated from the installed runtime package manifests after `npm ci`. These artifacts document package-specific metadata for the shipped runtime stack, but ThreatLens does not yet publish external supply-chain attestations.

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

The MIT/BSD family texts above are supplemental family references. Package-specific metadata is committed in `docs/reference/backend-runtime-package-metadata.json` and `docs/reference/frontend-runtime-package-metadata.json`. Built backend images also preserve wheel-provided notice files and upstream package metadata under the installed Python `.dist-info/` directories.

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
- Docker images built from this repository also install transitive Python and npm dependencies resolved from the lockfiles. The full resolved runtime inventories are committed under `docs/reference/` and can be regenerated with the command above.
- Built backend images ship `LICENSE`, `THIRD_PARTY_NOTICES.md`, the bundled license texts, `backend-requirements.txt`, `backend-requirements-lock.txt`, and `backend-runtime-dependencies.txt` under `/usr/share/doc/threatlens/`.
- Built backend images also ship `backend-runtime-package-metadata.json` under `/usr/share/doc/threatlens/`.
- Built web images ship `LICENSE`, `THIRD_PARTY_NOTICES.md`, the bundled license texts, `frontend-package-lock.json`, `frontend-runtime-dependencies.txt`, and `frontend-runtime-package-metadata.json` under `/usr/share/doc/threatlens/`.
- Built backend images also preserve upstream package metadata and wheel-provided notice files in the installed Python distribution directories under `/usr/local/lib/python3.12/site-packages/*.dist-info/`.
- Apache-2.0 third-party components use the standard Apache 2.0 license text already shipped as the repository `LICENSE`.
- The backend dependency spec installs `psycopg[binary]`, which pulls in both `psycopg` and `psycopg-binary`. Redistributors should preserve the shipped license texts, the backend lockfile, and the upstream wheel metadata already present in the image. If your compliance program requires a locally linked PostgreSQL client or a different LGPL fulfillment path, rebuild from `backend/requirements.txt` with non-binary `psycopg` before redistribution.
- ThreatLens redistributes `psycopg-binary` as received from PyPI at the version pinned in `backend/requirements-lock.txt`. Downstream redistributors should review the upstream project source/wheel publication path and satisfy any corresponding LGPL source-offer or relinkability obligations required by their distribution model.
- If you redistribute ThreatLens images or other packaged artifacts, preserve this notice file and comply with the licenses of bundled and transitive dependencies.
