# Development image builds

Run these commands from the repository root with Bash and Docker available:

```bash
./docker/build.sh           # Build both custom images
./docker/build.sh backend   # Build only the backend
./docker/build.sh web       # Build only the frontend
```

The helper builds local images without starting services or loading `.env`.
Building requires access to the base-image and dependency registries unless the
needed layers are cached. It resolves paths from its own location, so it also
works as `./build.sh web` from this directory or by absolute path elsewhere.

| Dockerfile | Build context | Default image | Compose services |
| --- | --- | --- | --- |
| [backend.Dockerfile](backend.Dockerfile) | `backend/` | `threatlens-backend:dev` | `api`, `worker`, `worker-ai`, `worker-maintenance`, `worker-notifications`, `beat` |
| [web.Dockerfile](web.Dockerfile) | `web/` | `threatlens-web:dev` | `web` |

PostgreSQL and Redis use upstream images. Each build context retains its own
`.dockerignore`; the repository root is not sent as the build context.

Check the resulting images:

```bash
docker image inspect threatlens-backend:dev threatlens-web:dev \
  --format '{{join .RepoTags ", "}} {{.Id}}'
```

## Run the development stack

For a new checkout, generate the runtime configuration with `./bootstrap.sh`.
After building both images, start them with the source-build override:

```bash
docker compose -f docker-compose.yml -f docker-compose.build.yml \
  up -d --no-build --pull never
```

This command requires the PostgreSQL and Redis images to be present too. On the
first run, fetch them with `docker compose pull db redis`, or build and start
everything together:

```bash
docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build
```

Open `http://localhost:3000` (or your configured `THREATLENS_WEB_PORT`).
Keep including both Compose files for development-stack operations. The base
Compose file alone selects the published GHCR images.

After editing only the frontend, rebuild and recreate just that service:

```bash
./docker/build.sh web
docker compose -f docker-compose.yml -f docker-compose.build.yml \
  up -d --no-deps --no-build --pull never web
```

After backend changes, rebuild the shared image and recreate all its services:

```bash
./docker/build.sh backend
docker compose -f docker-compose.yml -f docker-compose.build.yml \
  stop api beat worker worker-ai worker-maintenance worker-notifications
docker compose -f docker-compose.yml -f docker-compose.build.yml \
  up -d --no-build --pull never
```

The API runs migrations before workers start. For the first upgrade to lifecycle
policies, follow the ordered [lifecycle queue cutover](../docs/reference/configuration.md#lifecycle-queue-cutover).

## Build options and metadata

```bash
./docker/build.sh web --no-cache
./docker/build.sh all --pull
./docker/build.sh backend --platform linux/amd64
```

`--platform` accepts a single target supported by your Docker builder. Building
for another CPU architecture may require emulation. Release CI builds and
publishes the multi-architecture images separately.

To use a custom local tag, export it for both the helper and Compose:

```bash
export THREATLENS_DEV_IMAGE_TAG=my-branch
./docker/build.sh
docker compose -f docker-compose.yml -f docker-compose.build.yml \
  up -d --no-build --pull never
```

| Exported variable | Helper default |
| --- | --- |
| `THREATLENS_DEV_IMAGE_TAG` | `dev` |
| `THREATLENS_BUILD_VERSION` | Checked-in `VERSION` |
| `BUILD_DATE` | Current UTC time |
| `VCS_REF` | Checked-out Git commit, or `unknown` without Git metadata |
| `WEB_VITE_API_BASE_URL` | `/api/v1` |

The helper reads exported variables only. Compose also reads `.env`, so export
any customized values that should apply to both workflows. `THREATLENS_IMAGE_TAG`
continues to select published images; it does not select development images.
For direct Compose builds, the version defaults to the checked-in Compose value,
and the build date and revision default to `unknown` unless exported.

## Build directly with Docker

From the repository root:

```bash
docker build -f docker/backend.Dockerfile -t threatlens-backend:dev backend
docker build -f docker/web.Dockerfile -t threatlens-web:dev web
```

These use the same Dockerfiles and contexts as the helper, Compose, and CI.
Use `--build-arg` to set metadata when building directly; the helper fills those
values automatically.
