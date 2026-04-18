# Contributing

## Scope

ThreatLens is a security-oriented self-hosted product. Contributions should favor secure defaults, clear operator behavior, and incremental changes that are easy to review and test.

## Workflow

1. Start from a current branch based on `main`.
2. Keep changes focused. Separate refactors, behavior changes, and docs updates when possible.
3. Add or update tests when behavior changes.
4. Update docs when routes, settings, or deployment steps change.

## Local Checks

Backend:

```bash
./backend/.venv/bin/pytest backend/tests -q
```

Frontend:

```bash
docker run --rm -v "$PWD/web:/workspace" -w /workspace node:22 \
  bash -lc "npm ci && npm test && npm run lint && npm run build"
```

Compose rendering:

```bash
cp .env.example .env
docker compose config >/dev/null
```

## Pull Requests

- Explain what changed and why.
- Call out migrations, config changes, or operator-visible behavior changes.
- Include verification steps you ran.
- Flag security-sensitive changes explicitly.

## Design Expectations

- Prefer secure-by-default behavior.
- Fail clearly when queues, brokers, or remote dependencies are unavailable.
- Avoid silent operator-facing state drift in the UI.
- Do not remove existing safeguards without documenting the tradeoff.

## Communication

If you are unsure whether a change belongs in ThreatLens, open an issue or draft PR early with the problem statement, proposed approach, and rollout considerations.
