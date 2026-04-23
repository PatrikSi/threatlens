# Contributing

Thank you for helping improve ThreatLens.

## Before You Start

- Read [README.md](README.md) for the current local run flow.
- Read [SECURITY.md](SECURITY.md) before reporting a vulnerability or security-sensitive bug.
- Follow the expectations in [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## Development Basics

- Backend: FastAPI + SQLAlchemy + Celery in `backend/`
- Frontend: React + Vite in `web/`
- Default local stack: `docker compose up --build`

If `./backend/.venv` does not exist yet:

```bash
python3 -m venv backend/.venv
./backend/.venv/bin/pip install -r backend/requirements-dev.txt
```

Useful validation commands:

```bash
./backend/.venv/bin/pytest backend/tests -q
docker build -q -f web/Dockerfile web
```

## Pull Request Expectations

- Keep changes scoped. Small focused PRs are easier to review and safer to merge.
- Update docs when behavior, configuration, or the user-visible contract changes.
- Add or update tests when the change affects runtime behavior.
- Do not commit secrets, credentials, or real production data.
- Keep examples on the published API base path `/api/v1`. The only public-path exception is the schema endpoint at `/api/openapi.json`.

## Docs and Compliance Changes

If your change ships new dependencies, bundled assets, or new runtime behavior, update the related artifacts in the same PR:

- `README.md` and `docs/`
- `.env.example`
- `THIRD_PARTY_NOTICES.md`
- `docs/licenses/*` when bundled family license texts need to be added or refreshed
- `docs/reference/backend-runtime-package-metadata.json`
- `docs/reference/frontend-runtime-package-metadata.json`
- `docs/reference/frontend-runtime-package-legal/`
- `docs/reference/frontend-os-package-legal/`

If you add bundled font or media assets, document the exact upstream source and license terms. If you change packaged backend dependencies, re-check whether redistribution guidance needs to change. If you change packaged frontend dependencies, regenerate the frontend package-legal bundle instead of hand-editing files under `docs/reference/frontend-runtime-package-legal/`.

If your change affects the published API contract, regenerate the checked-in API artifacts:

```bash
./backend/.venv/bin/python backend/scripts/generate_api_reference.py
```

If your change affects shipped dependencies, bundled assets, or release-compliance metadata, regenerate the release artifacts with the same workflow used for a public release:

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

Before merging release-contract changes, review at least:

- `THIRD_PARTY_NOTICES.md`
- `docs/reference/api.md`
- `docs/reference/openapi.json`
- `docs/reference/backend-runtime-dependencies.txt`
- `docs/reference/frontend-runtime-dependencies.txt`
- `docs/reference/backend-runtime-package-metadata.json`
- `docs/reference/frontend-runtime-package-metadata.json`
- `docs/reference/frontend-runtime-package-legal/`
- `docs/reference/frontend-os-package-legal/`
- `docs/reference/release-process.md`
- `backend/requirements-lock.txt`
- `backend/compliance/`
- `web/compliance/`

## Commit Hygiene

- Use clear commit messages that describe the user-visible or operator-visible change.
- Avoid unrelated formatting churn.
- Do not rewrite someone else's in-flight work unless the PR explicitly includes that coordination.

## Questions

For non-sensitive product questions, open an issue at `https://github.com/PatrikSi/threatlens/issues` or draft a pull request at `https://github.com/PatrikSi/threatlens/pulls`.

For security-sensitive concerns, follow `SECURITY.md`. Do not post exploit details, credentials, or proof-of-concept data in a public issue or pull request.
