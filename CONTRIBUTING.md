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
- `docs/licenses/*` when bundled license texts need to be added or refreshed

If you add bundled font or media assets, document the exact upstream source and license terms. If you change packaged backend dependencies, re-check whether redistribution guidance needs to change.

## Commit Hygiene

- Use clear commit messages that describe the user-visible or operator-visible change.
- Avoid unrelated formatting churn.
- Do not rewrite someone else's in-flight work unless the PR explicitly includes that coordination.

## Questions

For product or security-sensitive questions, prefer opening a draft PR or emailing `patrik@local` instead of guessing at the contract in public issue threads.
