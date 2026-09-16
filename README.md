# ThreatLens

ThreatLens is a self-hosted app for tracking security (or any other) RSS feeds and articles.

It stores feeds, extracts article text, and gives a single pane of glass to review articles, alerting matches, optional AI summaries/recommendations. 

## Screenshots

![ThreatLens dashboard showing RSS triage and a daily brief panel](image.png)

| Original article preview | RSS-only triage view |
|---|---|
| ![ThreatLens original article preview drawer opened from an RSS item](image-1.png) | ![ThreatLens RSS-only dashboard layout with filters and item triage controls](image-2.png) |

## Features

- RSS feed collection and article extraction
- Read/starred state, notes, tags, and saved dashboard views
- Versioned alert rules, durable occurrences, analyst triage, suppression, backfill,
  dead-letter replay, and retained metrics
- Collaborative investigation collections with members, evidence snapshots, notes,
  lifecycle, and activity history
- Filtered article export as CSV, JSONL, ThreatLens ZIP, STIX 2.1, MISP, or readable PDF bundles
- Feed backup/restore plus webhook and multi-hook SMTP notifications
- Role-based users: `admin`, `analyst`, and `viewer`, with scoped API tokens and
  audit logs
- OpenID Connect SSO with account linking, verified-email JIT provisioning,
  claim-to-role mapping, revocable sessions, and local TOTP MFA
- Permission-gated operations diagnostics plus verified PostgreSQL backup, isolated
  restore-drill, and post-restore quarantine tooling
- Selective data lifecycle policies with aggregate previews, safeguards, bounded
  cleanup, cancellation, and auditable run history
- Durable integration outbox, bounded retries, dead-letter replay, circuit breaking, and delivery metrics
- Optional AI summaries, relevance scoring, task history, and daily briefs
- Prompted, sourced intelligence reports with templates, schedules, context-safe chunking, and Markdown/HTML/PDF artifacts

## Quick Start

For a new installation, use a matching repository checkout with Bash, Docker
Engine and Docker Compose v2 installed. Bootstrap needs either OpenSSL or
Python 3 to generate secrets. Create a local environment file:

```bash
./bootstrap.sh
```

The script creates `.env` and prints the generated admin login; it does not
start containers. The file includes every setting and comment from `.env.example`,
including AI, authentication, logging, retention and worker configuration. It
uses local HTTP defaults with AI disabled and initial admin seeding enabled.
To choose your own admin identity, run:

```bash
ADMIN_EMAIL=you@example.com ADMIN_PASSWORD='use-a-long-password' ./bootstrap.sh
```

Bootstrap escapes custom credentials for Compose, preserving literal dollar
signs, quotes and backslashes. Keep the generated file private.

For a production or internet-facing deployment, review `.env.example` and replace any local-only settings before first startup.

Pull the latest published images and start everything:

```bash
docker compose pull
docker compose up -d --wait
```

If you choose a custom output filename on first run, pass it to every Compose
command:

```bash
./bootstrap.sh threatlens.env
docker compose --env-file threatlens.env pull
docker compose --env-file threatlens.env up -d --wait
```

The default `latest` tag follows the newest published default image, and the bundled compose file asks Docker to refresh ThreatLens application images during `up`. To pin images to a published commit, first check out that commit and confirm its **Publish container images** workflow succeeded. Then use its SHA tag so the Compose configuration and images match:

```bash
export THREATLENS_IMAGE_TAG="sha-$(git rev-parse HEAD)"
docker compose pull
docker compose up -d --wait
```

Or build and start local development images from source:

```bash
THREATLENS_BUILD_VERSION="$(cat VERSION)" docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build
```

Both custom Dockerfiles live in [`docker/`](docker/README.md). To build images
without starting services or configuring `.env`:

```bash
./docker/build.sh           # Both images
./docker/build.sh backend   # API, workers, and scheduler image
./docker/build.sh web       # Web frontend image
```

These commands produce `threatlens-backend:dev` and `threatlens-web:dev`, which
the source-build Compose override uses. See the [image build guide](docker/README.md)
for running existing builds, custom tags, and rebuilding individual services.

Open the app:

```text
http://localhost:3000
```

Log in with `ADMIN_EMAIL` and `ADMIN_PASSWORD`.

After the first admin account exists, set `SEED_ADMIN_ON_STARTUP=false` for normal use.

For upgrades, retain the existing environment, credentials and encryption keys.
Bootstrap refuses to overwrite an existing file by default. `--force` explicitly
regenerates all secrets and is unsuitable for updating an initialized deployment;
follow the [database upgrade instructions](docs/pages/database-privileges.md#existing-installations-explicit-offline-cutover)
when moving from a single database role.

## Portainer

You can paste `docker-compose.yml` into a Portainer stack without uploading a
`.env` file or placing a database initialization script on the Docker host.
The Compose file includes the initialization script for new database volumes.

From the matching repository checkout, generate pasteable Compose environment
mappings. This mode requires Python 3 and Docker Compose v2; rendering does not
require a running Docker daemon:

```bash
./bootstrap.sh --print-compose-env
```

Replace the `x-db-environment`, `x-redis-environment`, `x-migration-environment`, and `x-backend-environment` blocks at the top of `docker-compose.yml` with the full output before deploying the stack. To choose your own admin identity, run:

```bash
ADMIN_EMAIL=you@example.com ADMIN_PASSWORD='use-a-long-password' ./bootstrap.sh --print-compose-env
```

Treat the generated mappings as secrets and preserve their escaping when
pasting. They use the current Compose defaults, including database connection
budgets and proxy settings. Keep the existing credentials and `APP_DATA_ENCRYPTION_KEY`
stable across upgrades; generating another mapping does not update roles or
passwords in an existing database volume.

The generated mapping is for HTTP-only local or LAN testing:

```text
APP_ENV: 'development'
AUTH_COOKIE_SECURE: 'false'
```

For HTTPS or internet-facing deployments, review `.env.example` before first startup.

The generated mapping includes explicit internal `DATABASE_URL` and `REDIS_URL` values so Portainer does not need separate stack variables.
For `.env`-based deployments, set `THREATLENS_WEB_PORT` if port `3000` is already in use and `THREATLENS_IMAGE_TAG` if you want a pinned release; for paste-only Portainer deployments, edit the `web.ports` entry or image tags in the compose file.

The 2.0.1 web image fixes the nginx startup permission error inside the container
and supports existing writable-root stack definitions without adding mounts.
In Portainer, update any old image pin, pull the corrected image and recreate
the web container; see the [web startup repair](docs/pages/runtime-budgets.md#web-startup-permission-denied-after-an-upgrade)
for the published image channels and commands. Read-only deployments retain
their writable tmpfs paths. Preserve existing credentials, encryption keys and
persistent volumes. A complete upgrade from 1.x still requires the
[2.0 upgrade procedure](docs/releases/2.0.0.md#upgrade-from-1x).

## Kubernetes configuration

Export each workload's resolved environment from an existing `.env` as a
Kubernetes Secret. This requires Python 3 and Docker Compose v2; it does not
require a Docker daemon or contact a cluster:

```bash
umask 077
python3 scripts/export_kubernetes_secret.py \
  --env-file .env --service api --name threatlens-api --namespace threatlens \
  > threatlens-api-secret.json
```

Set cluster database/Redis URLs, HTTPS, host and proxy settings in the input file
before exporting. The output contains plaintext secret values; keep it private
and out of source control. The exporter preserves configured values and does
not generate credentials or deploy workloads. See the
[Kubernetes environment reference](docs/reference/configuration.md#kubernetes-environment-export)
for AI worker and migration exports, `envFrom` usage, and the separate deployment
requirements.

## AI

AI is disabled by default.

To enable it, set:

```bash
AI_ENABLED=true
```

Then open **Settings -> AI** and configure an OpenAI-compatible endpoint, model, and API key if needed.
For Ollama, use either the server origin such as `http://192.168.0.113:11434` or the explicit OpenAI-compatible base `http://192.168.0.113:11434/v1`.

The generated `.env` includes all global AI environment settings. Named
providers, their keys, models, feature routes and model limits are configured in
the application and stored in PostgreSQL. `AI_API_KEY` and
`AI_API_KEY_BASE_URL` provide the optional legacy server key and its trusted
origin; they do not define the named-provider list.

If your AI provider is on a private network, also set:

```bash
ALLOW_PRIVATE_NETWORK_AI=true
```

ThreatLens works without AI.

## Useful Commands

For the 2.0 upgrade, read the [release notes and compatibility changes](docs/releases/2.0.0.md)
before replacing an existing installation.

Existing installations using a single database role must first complete the
[offline database-role cutover](docs/pages/database-privileges.md#existing-installations-explicit-offline-cutover).
The current Compose configuration requires separate runtime and migration
credentials; the commands below apply after that cutover. Keep existing database
credentials, encryption keys and volumes. Back up before applying migrations.

Update to the latest published images:

```bash
docker compose pull
docker compose stop --timeout 300 api beat worker worker-ai worker-exports worker-maintenance worker-notifications
docker compose up -d
```

Update to a pinned release:

```bash
THREATLENS_IMAGE_TAG=1.0.0 docker compose pull
docker compose stop --timeout 300 api beat worker worker-ai worker-exports worker-maintenance worker-notifications
THREATLENS_IMAGE_TAG=1.0.0 docker compose up -d
```

Stopping every API and worker process before recreation is required for schema
compatibility. PostgreSQL, Redis, and the web proxy may remain running; the web
interface will report the brief API outage until the matching release starts.
The first upgrade to database-backed lifecycle policies requires the ordered,
quiesced [lifecycle queue cutover](docs/reference/configuration.md#lifecycle-queue-cutover)
so an older maintenance process cannot race the new policies.
Migration `0086_classification_versions` also requires the stopped-writer
[classification recovery cutover](docs/reference/pipeline.md#classification-recovery-cutover)
and scans retained article text once to reconcile historical processing state.
The [team](docs/pages/teams.md) and [report review](docs/pages/reporting.md)
migrations also require every API and worker to be replaced together. New report
jobs use the editorial queue; upgraded AI workers must consume
`ai,ai-reports-v2,ai-reports-v3`. Existing reports and schedules retain their
publication policy, while new reports and schedules require review by default.

Check services:

```bash
docker compose ps
```

View logs:

```bash
docker compose logs -f api
docker compose logs -f worker
docker compose logs -f worker-ai
docker compose logs -f worker-maintenance
docker compose logs -f worker-notifications
docker compose logs -f beat
docker compose logs -f web
```

For temporary deep diagnostics, set `LOG_LEVEL=DEBUG` and `LOG_DETAIL=verbose` in `.env`, then recreate the API, workers, and Beat. Set `LOG_FORMAT=json` for structured collectors, or use `LOG_LEVEL_OVERRIDES=logger.name=DEBUG` for a focused subsystem. ThreatLens excludes request bodies, task argument values, and credential-bearing headers and redacts common secret patterns even in verbose mode; see [Configuration](docs/reference/configuration.md#diagnostic-logging) for the complete controls.

If the first startup fails with `Role "threatlens" does not exist`, an old PostgreSQL volume was likely initialized before the generated `.env` was in place. For a new install with no data to keep, reset the local volumes:

```bash
docker compose down -v
docker compose up -d
```

Run migrations:

```bash
docker compose exec api alembic upgrade head
```

Stop the stack:

```bash
docker compose down
```

Stop and remove the database/Redis volumes too:

```bash
docker compose down -v
```

## Local Development

Backend:

```bash
cd backend
python3 -m venv .venv
./.venv/bin/pip install -r requirements-dev.txt
./.venv/bin/alembic upgrade head
./.venv/bin/uvicorn app.main:app --reload
```

Worker and scheduler:

```bash
cd backend
./.venv/bin/celery -A app.tasks.celery_app.celery_app worker --loglevel="${LOG_LEVEL:-INFO}" --queues=default,ingest,processing -n 'worker@%h'
./.venv/bin/celery -A app.tasks.celery_app.celery_app worker --loglevel="${LOG_LEVEL:-INFO}" --concurrency=1 --queues=ai,ai-reports-v2,ai-reports-v3 -n 'ai@%h'
./.venv/bin/celery -A app.tasks.celery_app.celery_app worker --loglevel="${LOG_LEVEL:-INFO}" --queues=maintenance,lifecycle-v1 -n 'maintenance@%h'
./.venv/bin/celery -A app.tasks.celery_app.celery_app worker --loglevel="${LOG_LEVEL:-INFO}" --queues=notifications -n 'notifications@%h'
./.venv/bin/python -m app.tasks.beat_watchdog
```

Frontend:

```bash
cd web
npm ci
npm run dev
```

## Tests

Backend:

```bash
cd backend
./.venv/bin/python -m pytest
```

Frontend:

```bash
cd web
npm ci
npm test
npm run lint
npm run build
```

## Notes

- The default Docker setup runs PostgreSQL, Redis, the API, worker, scheduler, and web UI.
- Published application images can be pinned with `THREATLENS_IMAGE_TAG`; `latest` tracks the newest default published image, while release tags and `sha-*` tags are immutable references.
- The browser talks to the API through `/api/v1`.
- Feed/article fetching, AI calls, webhook and SMTP delivery, and OIDC provider communication can make outbound network requests.
- Private-network outbound access is off by default. Enable only what you trust in `.env` or your stack environment.
- OIDC requires HTTPS by default. `ALLOW_INSECURE_HTTP_OIDC=true` is intended only for isolated local development; private IdPs remain separately controlled by `ALLOW_PRIVATE_NETWORK_OIDC`.
- Keep `APP_DATA_ENCRYPTION_KEY` safe. Stored feed, webhook, delivery, and OIDC client-secret data depends on it.
- Use `.env.example` as the configuration reference.

## License

Apache-2.0. See [LICENSE](LICENSE).
