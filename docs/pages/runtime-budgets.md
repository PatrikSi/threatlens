# Runtime resource and database budgets

The bundled Compose stack limits CPU, memory, swap and process counts for every
service. Runtime containers have a read-only root filesystem, no Linux
capabilities and `no-new-privileges`. PostgreSQL and Redis retain only the five
capabilities their official entrypoints need to initialize volume ownership and
change user. Backend images already run as `app`; web now runs as `nginx` on
unprivileged port 3000. Docker host access remains an administrative boundary.

## Default host allocation

Treat these limits as a starting profile for a host with at least 16 GiB RAM.
They are ceilings, not proof of supported production throughput. The steady
state memory ceilings total 10.75 GiB, plus 512 MiB during migration. Leave room
for the host, Docker, filesystem cache and any other workloads. CPU quotas can
overcommit the host; relative shares give API and ingestion priority over export
generation when CPU is contended. Dedicated export slots and quotas limit its
impact, but disk/network contention still requires measurement on the deployment
hardware using the [capacity workload](../reference/capacity-baseline.md).

| Service | CPU ceiling | Memory ceiling | PID ceiling |
| --- | ---: | ---: | ---: |
| PostgreSQL | 2 | 2 GiB | 256 |
| Redis | 1 | 512 MiB | 128 |
| API | 2 | 1 GiB | 256 |
| Ingestion/processing worker | 2 | 2 GiB | 256 |
| Export worker | 1 | 2 GiB | 128 |
| AI worker | 1 | 1 GiB | 128 |
| Maintenance worker | 1 | 768 MiB | 128 |
| Notification worker | 1 | 1 GiB | 256 |
| Beat | 0.5 | 384 MiB | 96 |
| Web | 0.5 | 128 MiB | 64 |
| Migration, while upgrading | 1 | 512 MiB | 64 |

CPU and memory settings use the service-prefixed variables inventoried in
`.env.example`. Use a Compose override to change PID or temporary filesystem
limits. Keep `memswap_limit` equal to `mem_limit` if swap isolation is required;
verify the Docker host actually supports memory and swap accounting.

Backend temporary files live in a 512 MiB `noexec,nosuid` tmpfs; export workers
have 1 GiB. tmpfs usage consumes the container's memory allowance. A single
container may reject a large/concurrent export before the application-wide
export byte cap: lower accepted export limits or raise that service's memory
and temporary storage together after measurement. Generated artifacts are
stored durably in the database. Beat's disposable schedule file now lives under
`/tmp`; durable claims, schedule versions and recovery handle restart. Web may
write only bounded nginx temporary files and its generated configuration.
PostgreSQL and Redis retain their persistent named data volumes.

## Web startup permission denied after an upgrade

Upgrade the web container to the **2.0.1 build**, which removes the inherited
root-owned `default.conf` from the image. It supports older writable-root
Compose and Portainer definitions without adding mounts, while continuing to
run as non-root `nginx` (UID/GID 101). Keep the writable tmpfs paths below when
using a read-only root filesystem.

After the 2.0.1 image publishing workflow succeeds, pull the corrected `latest`
image and recreate only the web service:

```bash
THREATLENS_IMAGE_TAG=latest docker compose pull web
THREATLENS_IMAGE_TAG=latest docker compose up -d --no-deps --force-recreate web
docker compose logs --tail=100 web
docker compose ps web
```

These commands use the bundled `THREATLENS_IMAGE_TAG` variable. Update any
persistent image pin in the existing environment or stack definition too, so
the next deployment retains the fix. If `web.image` contains a literal old tag
or digest, change that reference before running the commands. Main-branch
publishing promotes `latest`, `main` and `sha-<commit>`; a numeric `2.0.1` image
tag is available only after a formal version-tag release. Use the published
`sha-<commit>` reference when a fixed deployment pin is required.

In Portainer, update an old image pin in the existing stack definition or its
tracked repository. Pull the corrected image and recreate the web container
(or redeploy the same stack with image re-pulling enabled). Pulling alone does
not replace a running container or change a saved image pin. Preserve existing
credentials, encryption keys and PostgreSQL/Redis data volumes. Check the web
container's logs and status: it should remain running without the permission
error, and the existing web URL should load.

### Workaround for the original 2.0.0 image and read-only deployments

The originally published 2.0.0 web image contains an inherited root-owned
`/etc/nginx/conf.d/default.conf` that its non-root `nginx` user (UID/GID 101)
cannot overwrite during startup. Older saved Compose or Portainer definitions
can expose this image defect as a restart loop with `Permission denied`.
If remaining pinned to that image, the bundled Compose settings below avoid
the conflict by mounting a writable tmpfs over that directory. These mounts
also provide the writable paths required by the bundled read-only runtime,
including with the corrected image.

Merge these settings into the existing `web` service, keeping its image, ports,
environment, networks and resource limits:

```yaml
services:
  web:
    read_only: true
    cap_drop: [ALL]
    security_opt:
      - no-new-privileges:true
    tmpfs:
      - /tmp:rw,noexec,nosuid,size=32m,mode=1777
      - /etc/nginx/conf.d:rw,noexec,nosuid,size=1m,uid=101,gid=101,mode=0755
```

Keep the image's non-root user. Replace older tmpfs entries for these paths and
remove conflicting bind mounts or volumes covering `/tmp`,
`/etc/nginx/conf.d` or its generated `default.conf`. Preserve the existing
database credentials, encryption keys and PostgreSQL/Redis data volumes.

For Compose, save the updated definition and recreate only the web service:

```bash
docker compose up -d --no-deps --force-recreate web
docker compose logs --tail=100 web
docker compose ps web
```

For Portainer, save these settings in the existing stack definition or its
tracked repository and redeploy that same stack. Pulling images alone does not
update its saved configuration.

This web startup repair preserves the database and requires no schema changes.
Installations upgrading from 1.x must separately follow the
[2.0 upgrade requirements](../releases/2.0.0.md#upgrade-from-1x), including the
database-role cutover and coordinated API/worker upgrade.

## Database connection inventory

`DATABASE_POOL_SIZE=2` and `DATABASE_MAX_OVERFLOW=0` apply per process. The API
overrides them to 8 + 2 via `API_DATABASE_POOL_SIZE` and
`API_DATABASE_MAX_OVERFLOW`. Export processes use `EXPORT_DATABASE_POOL_SIZE=4`
for concurrent claim renewal and artifact publication. Prefork children discard
inherited pools without closing parent-owned connections.

For the default single-replica topology, a conservative inventory is:

| Consumer | Maximum allocated connections |
| --- | ---: |
| API process | 10 |
| Five Celery parents and 11 worker children | 36 |
| Beat | 2 |
| Concurrent worker healthcheck subprocesses | 12 |
| Migration | 2 |
| AI admission, API and one AI worker child | 2 |
| Total, including conservative probe/parent allowances | 64 |

AI workload admission uses a separate pool with one connection, zero overflow,
and a one-second checkout limit per application engine/process. It is opened
only when a provider workload limit is enabled. The default topology reserves
one additional connection for the API and one for the AI worker child; add one
for every additional process or workload harness that executes limited AI calls.
These slots are separate from `DATABASE_POOL_SIZE`, preventing admission from
waiting for a second connection in a saturated request pool. Forked children
detach inherited admission pools without closing parent-owned connections.
Admission connection establishment is capped at two seconds and its database-only
transactions share a three-second deadline. No admission row lock spans provider
I/O. Each reservation's absolute lifetime bounds the synchronous provider call,
so a paused worker cannot resume an expired reservation with a fresh timeout.

The bundled PostgreSQL default is 100 connections. Keep spare connections for
recovery and administration; do not use the superuser reserve for normal work.
Multiply every process allocation when adding replicas or increasing worker
concurrency. Increasing a pool can reduce local queueing while exhausting the
database's global slots. Validate the total before deploying. Healthcheck and
parent allocations above are deliberately conservative; most use no SQL.

## Lock and operation deadlines

Runtime connections use a five-second lock acquisition timeout and the existing
30-second statement timeout. Pool checkout is bounded to ten seconds. Lock
timeouts limit waiting for a lock; they never silently release an authorization
lock already held by a transfer or external action.

`database_operation` gives one database-only transaction a shared monotonic
allowance, reduces each SQL statement's timeout to the remaining allowance,
checks before commit, and rolls back on failure. Repair and lifecycle batches
use this boundary. Each lifecycle transaction includes its policy checks,
history changes, progress update and commit in the same allowance; initial run
claims have a separate allowance. Deadline expiry returns the run to its durable
retry queue. Rollback is allowed after expiry, including savepoint rollback. An acknowledged commit is not retroactively reported as failed, and
each subsequent transaction needs a new scope. This is not a mechanism for
interrupting arbitrary Python code or external side effects.

Deferred constraints are flushed and evaluated within the remaining SQL budget
before commit; PostgreSQL transaction finalization would otherwise run that work
outside its ordinary statement timer. Final commit durability and network
acknowledgement can still have an uncertain outcome during storage/network
failure. TCP keepalives (30-second idle, ten-second interval, three probes) and a
60-second unacknowledged-data timeout bound dead connections on supported hosts.
They do not end a healthy connection holding an authorization fence.

Pool exhaustion and PostgreSQL lock, serialization, deadlock and deadline
failures produce a sanitized retryable `503 database_busy` with `Retry-After` at
the API boundary. Mutating clients must reuse their idempotency key or reload
the durable operation before retrying when completion is uncertain.

Authorization-fenced exports, AI providers and other external operations retain
their own transfer/request deadlines. PostgreSQL 16 does not offer a total
transaction timeout; no global short idle-transaction killer is installed,
because it could release an authorization fence while an external side effect
continues. The explicit [database role cutover](database-privileges.md) remains
required for existing installations.

## Disposable deployment verification

```bash
python3 scripts/verify_runtime_isolation.py --output /tmp/threatlens-isolation.json
```

The verifier builds only `git archive HEAD` contents in a private temporary
directory. It creates synthetic credentials and a unique Compose project,
checks actual cgroup limits and writable paths, probes readiness during export
CPU pressure, verifies a separate memory-limited process is OOM-killed, and
checks export-worker/Beat restart recovery. It removes only its own containers
and volumes. Local images and private diagnostic logs remain for inspection.
The JSON records the measured commit; this bounded smoke does not establish
production capacity or sustained throughput.
