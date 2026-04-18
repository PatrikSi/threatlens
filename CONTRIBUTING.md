# Contributing to ThreatLens

## Scope

ThreatLens is a self-hosted threat intelligence platform. Contributions should prioritize operator trust, secure defaults, and predictable behavior under failure.

## Before You Start

- Open an issue or discussion before large changes, refactors, or feature additions.
- Keep changes narrowly scoped and easy to review.
- Document any new config flags, migrations, or operator-visible behavior.
- Never commit real credentials, customer data, or sample secrets.

## Local Workflow

1. Copy the environment template:

```bash
cp .env.example .env
```

2. Install backend dependencies and run migrations:

```bash
cd backend
python -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/alembic upgrade head
```

3. Start the API:

```bash
./scripts/start-api.sh
```

4. Start worker and beat in separate shells:

```bash
./.venv/bin/celery -A app.tasks.celery_app.celery_app worker --loglevel=INFO
./.venv/bin/celery -A app.tasks.celery_app.celery_app beat --loglevel=INFO
```

5. For the frontend, use the `web` workspace:

```bash
cd web
npm install
npm run dev
```

## Required Checks

Run the checks relevant to your change before opening a pull request.

Backend:

```bash
./backend/.venv/bin/pytest backend/tests -q
```

Frontend:

```bash
cd web
npm test
npm run lint
npm run build
```

Compose validation:

```bash
docker compose --env-file .env.example config
```

## Change Guidelines

- Add or update tests when behavior changes.
- Prefer secure-by-default behavior over opt-in hardening.
- Treat docs as part of the feature. Update README, config docs, and UI references when routes or settings change.
- Preserve backward compatibility where practical; if not, call out operator impact clearly.
- Avoid destructive git history edits in pull requests.

## Pull Requests

Each pull request should include:

- a short summary of the user or operator problem being solved
- the implementation approach and any tradeoffs
- testing performed
- deployment, migration, or configuration impact
- security-sensitive considerations when applicable

Use the repository pull request template as the minimum checklist.
