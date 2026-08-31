# PostgreSQL Backup and Recovery

This runbook covers the host-side utility at
**scripts/recovery/threatlens-recovery.sh** for Docker Compose deployments.

PostgreSQL is the authoritative ThreatLens data store. Redis contains disposable
queue, lock, cache, and heartbeat state. **Never copy, archive, or restore the Redis volume.**
A completed database restore clears Redis before any application service may
resume.

## Recovery contract

The utility provides five noninteractive commands:

| Command | Purpose | Changes production data? |
|---|---|---|
| backup | Create an online PostgreSQL custom-format archive and manifest | No |
| verify | Validate the manifest, path, size, checksum, and pg_restore catalog | No |
| drill | Restore into a temporary isolated PostgreSQL container and run checks | No |
| restore | Replace the production database after explicit safety gates | Yes |
| reconcile | Classify and finish an interrupted destructive restore | Potentially |

Exit codes are stable for automation:

| Exit | Meaning |
|---|---|
| 0 | Completed successfully |
| 2 | Invalid CLI usage |
| 3 | Missing or invalid prerequisite/configuration |
| 4 | Manifest, path, version, or archive validation failure |
| 5 | Online database or backup operation failure |
| 6 | Isolated restore-drill failure or cleanup failure |
| 7 | Destructive restore refused by a safety gate |
| 8 | Destructive restore failed or requires operator escalation |

Messages include stable identifiers such as E402 plus a specific operator-facing
explanation. The scripts never enable shell tracing and do not place database,
Redis, or application encryption secrets in command arguments or logs.

When the system_operation_runs table is present and PostgreSQL is reachable,
backup, verify, and drill durably write a running row before doing their main
work, then update that same row to succeeded or failed. A later command under the
same private recovery-journal root and Compose project marks a crash-left running
row as failed with operation_interrupted before it begins. Rows from another
scope are never reconciled automatically. A failed
destructive restore is written to the original database only after rollback has
been reconciled successfully; no history write is attempted while database
identity is uncertain. The row contains
only allowlisted bounded metadata such as archive checksum, application version,
Alembic revision, byte or table count, and boolean checks. It never contains a
backup path, environment value, hostname, username, URL, or credential. History
recording is deliberately best effort: an otherwise successful backup,
verification, or drill remains successful if the ledger is unavailable.

Before the first destructive database statement, restore creates a private,
mode-0700 host journal root containing a mode-0600 phase record. Initial state is
written and fsynced in a private publication directory before that complete
directory is atomically renamed to `active`; an empty active journal is never a
publication marker. Every newly created journal-root path component is published
by fsyncing its parent directory. Each later phase is atomically replaced and
fsynced. The journal records the operation and archive identities, rendered deployment
identity, original and replacement PostgreSQL OIDs, access state, and final
outcome, but no credentials. It is archived only after matching database
operation evidence has been written. A live active journal blocks another
restore until an operator runs `reconcile`.
All recovery commands share a nonblocking advisory lock in that private root.
The kernel releases it on normal exit, signals, process death, or host restart, so
concurrent recovery work is refused without creating a stale lock after SIGKILL.
Terminal archival first publishes and fsyncs the immutable history record and a
validated terminal receipt, then atomically retires `active` through a private
cleanup marker. Restore, archive, and reconciliation recover abandoned
publication directories, legacy empty active directories, interrupted cleanup
markers, and the final cleanup-fsync ambiguity without discarding a published
phase record. The receipt is removed when the next restore starts its private
atomic publication; immutable history remains authoritative if that unpublished
initialization is interrupted.

The Operations view treats recovery evidence as related artifacts, not independent
green checks. Verification and drill cards, failures, incomplete states, and age
warnings are selected by the latest successful backup checksum. A newer failure
against an older archive remains visible in run history but does not mark the
current archive untrusted. The view warns when the latest backup is older than 26
hours, its correlated drill is older than 31 days, a correlated run remains
incomplete, or successful evidence covers a different checksum.
Recovery cards and their checksum correlation are read in one PostgreSQL statement,
so a concurrently inserted run cannot produce a mixed evidence snapshot.

The Operations encrypted-data component caches its probe for 60 seconds per API
process and inspects at most 500 recent rows in each encrypted-data category. Its
metrics expose `scan_complete`, `scan_limit_per_category`, `truncated_categories`,
`cache_hit`, and `inventory_scanned_at`; when truncated, record totals and
readability describe the bounded sample rather than the complete database. Use the
dedicated encrypted-data health endpoint when an exact full inventory is required.

Database storage figures in the Operations view come from PostgreSQL logical
size and retained-history growth. The API container cannot reliably inspect free
space on a separately mounted PostgreSQL volume or managed database host. Monitor
that filesystem with the Docker host, storage platform, or database service and
alert before free capacity reaches the restore and maintenance headroom required
by this runbook.

## Prerequisites

- Run on the Docker host as an account permitted to use Docker.
- Keep Docker Engine, Docker Compose v2, Bash, Python 3, and standard GNU
  host utilities available.
- Keep the Compose **db** service running and PostgreSQL ready for backup.
- Keep the PostgreSQL image used by **db** present locally for verify and drill.
  Run docker compose pull db if it is absent.
- Use a real readable environment file and Compose file. Recovery tooling rejects
  symlinks for these files and all backup artifacts.
- Provide enough space for the dump, an off-host copy, and, during actual restore,
  a private staged archive, an isolated drill database, a mandatory safety dump,
  and the temporary rollback database.

The built-in adapter intentionally supports only a Compose-local PostgreSQL URL
targeting **db:5432** and a Compose-local Redis URL targeting **redis:6379/0**.
The utility validates the rendered DATABASE_URL and REDIS_URL of the API and every
configured backend worker against the db and redis service credentials and shared
networks. It refuses external databases, external Redis, TLS/proxy URL options,
credential drift, or a nonzero Redis database. There is no generic external-target
adapter yet; do not work around this refusal by temporarily changing URLs.

Every rendered service carrying `DATABASE_URL` or `REDIS_URL` is treated as a
data accessor. Recovery refuses an accessor outside the bundled API, worker,
scheduler, and web service set, and stops every recognized accessor before
database fencing. It also compares the running containers' database, Redis, and
encryption environments with the rendered Compose model. A custom service must
not be added to a destructive recovery topology until the adapter and tests have
explicitly learned how to identify, stop, and verify it.

The bundled adapter also requires one PostgreSQL role to be both the container
administrator and application role. Separate least-privileged application and
administrative roles are intentionally unsupported and refused. Supporting that
topology requires a dedicated adapter with independently tested ownership,
fencing, rollback, and evidence credentials; do not broaden privileges or rewrite
URLs to bypass the refusal.

The POSTGRES_USER and POSTGRES_DB values rendered into the **db** service must
match the role and database that initialized the existing PostgreSQL volume.
Changing these values in .env does not rename roles or databases already stored
in that volume. If backup fails while reading the Alembic revision with a
"role does not exist" error, compare the rendered Compose environment with the
API DATABASE_URL, keep the volume intact, and have a PostgreSQL operator correct
the configuration or role ownership. Do not bypass the check or recreate the
volume as a troubleshooting shortcut.

Global options precede the command. The repository defaults are .env and
docker-compose.yml:

    ./scripts/recovery/threatlens-recovery.sh \
      --env-file /opt/threatlens/.env \
      --compose-file /opt/threatlens/docker-compose.yml \
      --project-name threatlens \
      backup --output-dir /srv/threatlens-backups

Repeat --compose-file in Compose precedence order for overrides:

    ./scripts/recovery/threatlens-recovery.sh \
      --env-file .env \
      --compose-file docker-compose.yml \
      --compose-file docker-compose.build.yml \
      backup --output-dir /srv/threatlens-backups

The same values can be supplied with THREATLENS_ENV_FILE, a colon-separated
THREATLENS_COMPOSE_FILE, THREATLENS_COMPOSE_PROJECT, and
THREATLENS_BACKUP_DIR.

## Create a backup

Run an online backup while the stack is serving traffic:

    backup_path="$(
      ./scripts/recovery/threatlens-recovery.sh \
        --env-file .env \
        --compose-file docker-compose.yml \
        backup --output-dir /srv/threatlens-backups
    )"
    printf 'Completed backup: %s\n' "$backup_path"

pg_dump takes a transactionally consistent PostgreSQL snapshot without stopping
the API. The utility writes into a restrictive
.threatlens-backup.partial.* directory, fsyncs the files and directory, and
atomically renames the completed directory. A directory containing manifest.json
is the publication marker. Interrupted partial directories are ignored by later
backups and rejected by verification; inspect and remove them only after
confirming no recovery process is active. When the configured backup root or any
missing ancestor is created, each new directory entry is made durable by fsyncing
its parent before backup work begins.

The completed directory and both files are restricted to the invoking account:

    threatlens-postgresql-20260827T120000Z-1a2b3c4d/
      database.dump
      manifest.json

The manifest contains:

- format and schema version;
- ThreatLens application version and Alembic revision;
- PostgreSQL server version and UTC snapshot start time;
- archive filename, byte size, custom format, and SHA-256;
- database size and selected pg_stat_user_tables row estimates;
- a truncated SHA-256 fingerprint of APP_DATA_ENCRYPTION_KEY when it can be
  read safely from the process environment or dotenv file;
- an explicit redis_included: false declaration.

The row estimates are advisory metadata collected close to the dump. They are not
exact counts from the dump transaction and must not be used as a completeness
proof.

### Schedule backups

Use the host scheduler of your choice and capture both output streams. A daily
systemd timer or cron job can invoke:

    cd /opt/threatlens && \
      ./scripts/recovery/threatlens-recovery.sh \
        --env-file /opt/threatlens/.env \
        --compose-file /opt/threatlens/docker-compose.yml \
        --project-name threatlens \
        backup --output-dir /srv/threatlens-backups

An atomically created mode-0700 **.threatlens-recovery.lock** directory prevents
overlap without following a pre-existing symlink. An interrupted host process can
leave a stale lock directory; verify that no recovery process is active before
removing it. Alert on every nonzero exit and on the absence of a recent completed
directory. Controlled failures of the mandatory restore safety backup clean its
partial directory and lock in the parent recovery process. Retention jobs must
ignore partial directories and the lock directory.

## Store backups safely

A local archive is not a disaster-recovery copy. After every successful backup:

1. Transfer the entire completed directory to encrypted off-host or offline
   storage.
2. Preserve database.dump and manifest.json together without renaming either.
3. Use immutable or object-locked retention where available. Separate deletion
   credentials from backup-writing credentials.
4. Keep a copy outside the host, storage account, and administrative failure
   domain of the running deployment.
5. Run verify after transfer and isolated restore drills on a schedule.

The backup intentionally excludes .env, Docker secrets, TLS keys,
APP_DATA_ENCRYPTION_KEY, APP_DATA_ENCRYPTION_PREVIOUS_KEYS, and Redis data.
Escrow current and previous encryption keys separately in an encrypted secrets
system. The manifest fingerprint helps select the right key without disclosing
it. A PostgreSQL archive without the matching key may restore successfully while
encrypted feed, integration, delivery, and OIDC fields remain unreadable.

## Verify an archive

Verify after creation, after transfer, and before every drill or restore:

    ./scripts/recovery/threatlens-recovery.sh \
      --env-file .env \
      --compose-file docker-compose.yml \
      verify \
      --backup /srv/threatlens-backups/threatlens-postgresql-20260827T120000Z-1a2b3c4d

For a release-specific gate, add --expected-app-version 1.9.0.

Verification rejects malformed or unsupported manifests, partial directories,
path traversal, symlinks, non-regular files, missing archives, size differences,
and checksum differences. It then starts the configured PostgreSQL image with no
network and runs pg_restore --list against the archive.

SHA-256 provides corruption detection and binds the archive to a trusted manifest;
it does **not** authenticate either file. An attacker who can replace both the
archive and manifest can create a new valid checksum. Protect backup storage with
separate access controls and immutability, and record or sign manifest checksums in
an independent system when authenticity against a storage administrator matters.
Before a drill or restore, the utility copies the approved manifest, archive,
hook, and manifest helper through no-follow file descriptors into a private
mode-0700 directory, fsyncs the copies, and re-hashes them immediately before each
restore or hook use. This closes pathname replacement races on the invoking host;
it does not defend against a compromised root account or kernel.

**Verification does not restore rows and is not a recovery drill.** It proves
that the envelope, checksum, and archive catalog are readable, not that PostgreSQL
can recreate the schema and data.

## Run an isolated restore drill

Run a true drill regularly and before relying on a new backup or PostgreSQL image:

    ./scripts/recovery/threatlens-recovery.sh \
      --env-file .env \
      --compose-file docker-compose.yml \
      drill \
      --backup /srv/threatlens-backups/threatlens-postgresql-20260827T120000Z-1a2b3c4d

The drill:

1. performs manifest, checksum, encryption-key fingerprint, target, and catalog
   validation, then privately stages all recovery inputs;
2. creates a random internal Docker network with no external route;
3. starts a temporary PostgreSQL container and data volume;
4. restores with exit-on-error and one transaction, and confirms the source
   Alembic revision matches the manifest;
5. starts the configured backend image only on that internal network, runs its
   complete `alembic upgrade head` path, queries required schema objects, and
   exercises the FastAPI `/v1/health/live` route in-process;
6. runs the exact staged quarantine hook preflight against the upgraded isolated
   database and requires an explicit isolated-target acknowledgement marker;
7. removes the backend container, PostgreSQL container, volume, private staging
   directory, and network on success or failure.

The temporary backend receives a generated, minimal environment: AI and all
private-network outbound features are disabled, provider credentials are omitted,
and the internal Docker network has no external route. No worker, scheduler, SMTP
connector, webhook connector, feed fetcher, or externally routable API runs in the
drill. Treat exit 6, including cleanup failure after successful checks, as a failed
drill. A custom hook must honor `THREATLENS_RECOVERY_DATABASE_CONTAINER`, user,
and database name and print both `QUARANTINE_PREFLIGHT=passed` and
`QUARANTINE_DATABASE_TARGET=isolated_container`.

Retain timestamped command output, archive SHA-256, Alembic revision, table count,
operator identity, Docker and PostgreSQL versions, ticket or change identifier,
duration, and cleanup confirmation as drill evidence.

Maintainers can exercise the complete destructive workflow against the dedicated,
internal-only disposable test project:

    THREATLENS_RUN_DOCKER_RECOVERY_E2E=1 \
      python3 -m unittest tests.recovery.test_recovery_docker_e2e -v

To test the current checkout rather than a published backend image, build it and
pass the resulting immutable image ID to the disposable Compose project:

    docker compose -f docker-compose.yml -f docker-compose.build.yml build api
    recovery_image="$(docker image inspect \
      --format '{{.Id}}' ghcr.io/patriksi/threatlens-backend:latest)"
    THREATLENS_RUN_DOCKER_RECOVERY_E2E=1 \
      RECOVERY_E2E_BACKEND_IMAGE="$recovery_image" \
      python3 -m unittest tests.recovery.test_recovery_docker_e2e -v

This test creates and deletes only the `threatlens-recovery-e2e` project and its
named volumes. Never repoint its Compose file or project name at a real deployment.

## Destructive restore safety gate

The repository includes the production default hook at
scripts/recovery/post_restore_quarantine.sh. The restore command selects it unless
THREATLENS_POST_RESTORE_HOOK or --quarantine-hook supplies a reviewed replacement.
The default hook refuses unsupported schemas, applies all mutations in one
PostgreSQL transaction under an advisory lock, and uses the archive checksum as
an idempotent audit marker. Restoring data and allowing old sessions, API tokens,
schedules, or connector deliveries to resume is not a completed recovery.

The hook is invoked with one positional phase and the same value in
THREATLENS_RECOVERY_PHASE:

| Phase | Required behavior |
|---|---|
| preflight | Read-only. Validate the manifest envelope, migrated restored schema, superuser fencing capability, and ability to perform every action below. |
| apply | In one transaction, revoke browser sessions and API tokens, consume one-time MFA login challenges, rotate legacy JWT generations, disable integrations, subscriptions, legacy webhooks, feeds, AI automation, and report schedules, terminalize queued/running notification, AI, report, and alert-evaluation work, and write a system audit event. |
| verify | Read-only. Fail unless every revocation and quarantine invariant is durably true. |

The hook also receives:

- THREATLENS_RECOVERY_COMPOSE_FILES, newline-delimited in precedence order;
- THREATLENS_RECOVERY_ENV_FILE;
- THREATLENS_RECOVERY_PROJECT_NAME;
- THREATLENS_RECOVERY_MANIFEST.
- THREATLENS_RECOVERY_ARCHIVE_SHA256, the checksum already validated by the
  parent recovery process. Direct hook invocations omit it and verify the
  archive independently;
- THREATLENS_RECOVERY_MANIFEST_HELPER, the privately staged helper;
- THREATLENS_RECOVERY_DATABASE_CONTAINER, DATABASE_USER, and DATABASE_NAME when
  preflight is bound to an isolated drill target.

The default hook does not contact external services, log secrets, enable any
integration or schedule, clear audit evidence, or report success after partial
completion. The packaged migration path upgrades the isolated copy and fenced
production target before the hook runs, so historical archives use the current
quarantine schema contract instead of brittle direct-schema branches. Exit zero
is accepted only after the phase is complete.

Override the default only with a reviewed hook using --quarantine-hook
/absolute/path/to/hook or THREATLENS_POST_RESTORE_HOOK. Symlink hooks are
rejected. Run the default or replacement preflight directly during change review
when needed; it is read-only.

## Perform an actual restore

Do not run actual restore until:

- the archive passed verify and a recent isolated drill;
- the separately escrowed encryption key matches the manifest fingerprint;
- space exists for the restored database, rollback database, and safety backup;
- the application quarantine hook was reviewed and preflight passes;
- an incident or change owner, rollback decision maker, and communications path
  are active;
- the measured outage fits the incident RTO decision.

Destructive restore requires an explicit Compose project. First render the exact
confirmation for the currently running database and Redis containers:

    confirmation="$(./scripts/recovery/threatlens-recovery.sh \
      --env-file /opt/threatlens/.env \
      --compose-file /opt/threatlens/docker-compose.yml \
      --project-name threatlens \
      restore \
      --backup /srv/threatlens-backups/threatlens-postgresql-20260827T120000Z-1a2b3c4d \
      --show-confirmation)"
    printf '%s\n' "$confirmation"

The text includes the project, database, full archive SHA-256, and a deployment
identity derived from stable live database/Redis container, image, and volume
identities. Restarting or replacing either container invalidates it. Review the
text, then provide it with the independent data-loss acknowledgement:

    ./scripts/recovery/threatlens-recovery.sh \
      --env-file /opt/threatlens/.env \
      --compose-file /opt/threatlens/docker-compose.yml \
      --project-name threatlens \
      restore \
      --backup /srv/threatlens-backups/threatlens-postgresql-20260827T120000Z-1a2b3c4d \
      --confirm "$confirmation" \
      --acknowledge-data-loss \
      --safety-backup-dir /srv/threatlens-backups/pre-restore

The backup application version must match the deployed repository by default.
Restoring an older archive for planned forward migration additionally requires
--allow-app-version-mismatch. The archive is upgraded by the configured backend
image in isolation first and again while the production target is fenced.
Both the PostgreSQL and backend tags are resolved to immutable local image IDs
before the isolated checks. Those IDs remain pinned for the entire operation, so
a concurrent pull or tag mutation cannot change the code or PostgreSQL binary
used after confirmation.

The manifest encryption-key fingerprint must match the active deployment.
`--acknowledge-encryption-key-mismatch` exists only for an incident where key
escrow recovery is intentionally deferred; it emits a warning and means encrypted
fields may remain unreadable. It is not a key migration mechanism.

The restore sequence is:

1. validate project identity, rendered local data targets, key fingerprint, both
   opt-ins, hook, manifest, checksum, version, and archive catalog;
2. privately stage and re-hash the inputs, restore them in isolation, run the
   packaged migration/API smoke, and preflight the exact hook there;
3. create a mandatory fresh online safety backup;
4. stop api, all workers, beat, and web, then prove they are stopped;
5. arm rollback before any mutation, create a short-lived random recovery role,
   set the application role NOLOGIN, disallow database connections, terminate
   existing clients, rename the original database, and create a target that only
   the recovery role can access;
6. restore transactionally, preserving the original database locale, tablespace,
   and connection limit, then run packaged migrations and API/schema smoke checks
   on an internal-only network while the application role remains fenced;
7. run hook preflight, apply, and verify; require Redis AOF persistence, temporarily
   set `appendfsync` to `always`, clear Redis database 0, and restore its previous
   append-fsync policy before durably journaling the clear;
8. reassign restored objects, copy the original database ACL and database/role
   settings, restore the application login/connectivity state, remove the
   temporary role, and prove a fresh application-role connection plus final
   catalog invariants while the original rollback database still exists; only
   then remove the rollback database.

The fence blocks ordinary PostgreSQL clients at both role and database levels.
PostgreSQL superusers can always bypass database ACLs; privileged database
administrators must stay out of the target during the documented maintenance
window. The recovery role password is generated in memory, transferred to Docker
through an environment-only exec boundary, stored only in a private temporary
smoke environment, and never written in a command argument or log.

The utility does not restart application services. Success prints
RESTORE_STATUS=completed_quarantined and the safety-backup path. Preserve that
output as incident evidence.

Start only the API and web UI for controlled inspection:

    docker compose up -d api web
    docker compose logs --since 10m api
    docker compose exec -T api alembic current

Confirm the expected version, migrations, encryption-key inventory, audit event,
revoked credentials, disabled integrations and feeds, disabled AI automation,
terminalized queues, and quarantined schedules. An administrator must deliberately
re-enable each approved feed, AI capability, integration, and schedule. Start
workers and beat only after review:

    docker compose up -d worker worker-ai worker-maintenance worker-notifications beat
    docker compose ps

## Rollback and escalation

Every failure after rollback is armed invokes state reconciliation before a failed
operation is recorded. Reconciliation probes the target and rollback database as
three distinct outcomes: present, missing, or probe error. It retries at most three
times and can recover when interruption occurred after rename but before clean
target creation. Application services remain stopped even after successful
rollback. Inspect PostgreSQL and Redis, retain logs, and make a new recovery
decision before restarting.

A **CRITICAL** reconciliation message means the original database identity or
access state could not be proven after the bounded attempts. No operation-ledger
write is made in that uncertain state. Keep all application processes stopped,
preserve Docker and PostgreSQL logs, list databases and role login state from the
db container, protect the safety archive, and escalate to a PostgreSQL operator.
Do not repeatedly rerun restore or manually drop an `tl_pre_restore_*` database or
`tl_recovery_*` role without identifying the last known-good state.

If the host receives INT, TERM, or HUP, or an unhandled shell failure occurs after
rollback is armed, the EXIT trap performs the same phase-aware reconciliation.
SIGKILL and host power loss cannot run a trap. The durable host journal is the
authoritative next-action record; do not infer the outcome from database names
alone. Once PostgreSQL and Redis are available, reconcile the exact project while
leaving all application accessors stopped:

    ./scripts/recovery/threatlens-recovery.sh \
      --env-file /opt/threatlens/.env \
      --compose-file /opt/threatlens/docker-compose.yml \
      --project-name threatlens \
      --journal-dir /srv/threatlens-recovery-journal \
      reconcile

`reconcile` compares project, rendered configuration, live deployment identity,
original and replacement database OIDs, access state, and the archive-bound
quarantine audit marker. It then either restores the proven original identity,
finishes a proven forward commit, or refuses with services stopped when the state
is unknown. A running journal whose durable phase is already `completed` can be
classified as a forward commit only when the replacement OID, final database state,
and archive-bound quarantine marker all prove that outcome. A terminal journal
whose evidence write was interrupted is ingested
idempotently by operation UUID and archived afterward. If interruption occurs
during journal publication, the next restore discards only the private unpublished
directory; if it occurs during terminal archival, `reconcile` or an archive retry
finishes the already-published history transition. A completed archive retry is a
no-op, so an empty legacy `active` directory cannot block both workflows. Keep
the journal root on durable host storage, mode 0700, outside ephemeral containers. The default is
`backups/recovery-journal`; production operators should select an explicitly
backed-up private path with `--journal-dir` or
`THREATLENS_RECOVERY_JOURNAL_DIR`.

E812 means quarantine completed but ACL/connectivity finalization did not. The
reconciler attempts to restore the original database when it still exists, or to
restore safe connectivity to the quarantined target if finalization had already
removed the rollback database. Services remain stopped in either case.

## RPO, RTO, and evidence

ThreatLens does not promise a universal recovery point or recovery time objective.
Effective RPO is at least the backup interval plus transfer lag. Effective RTO
includes decision time, archive retrieval, verification, restore, quarantine,
migration, validation, and deliberate service resumption. Database size, storage
throughput, image availability, and migration work can dominate it.

Measure both with representative data and recurring drills. Retain:

- backup paths, checksums, versions, timestamps, and storage copy IDs;
- backup, verification, and drill statuses and stable error identifiers;
- drill duration and proof temporary Docker resources were removed;
- restore approvals, chosen archive, safety-backup path, hook audit event, service
  stop and start times, validation results, and re-enable decisions;
- relevant host, Docker, PostgreSQL, API, and worker logs under the incident or
  change record retention policy.

Retest after PostgreSQL, Compose, encryption-key, migration, storage, or hook
changes. A backup process without a recent isolated restore drill is an unproven
recovery plan.
