# PostgreSQL Backup and Recovery

This runbook covers the host-side utility at
**scripts/recovery/threatlens-recovery.sh** for Docker Compose deployments.

PostgreSQL is the authoritative ThreatLens data store. Redis contains disposable
queue, lock, cache, and heartbeat state. **Never copy, archive, or restore the Redis volume.**
A completed database restore clears Redis before any application service may
resume.

## Recovery contract

The utility provides four noninteractive commands:

| Command | Purpose | Changes production data? |
|---|---|---|
| backup | Create an online PostgreSQL custom-format archive and manifest | No |
| verify | Validate the manifest, path, size, checksum, and pg_restore catalog | No |
| drill | Restore into a temporary isolated PostgreSQL container and run checks | No |
| restore | Replace the production database after explicit safety gates | Yes |

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

When the system_operation_runs table is present and PostgreSQL is reachable, each
command writes a succeeded or failed row for the Operations UI. The row contains
only allowlisted bounded metadata such as archive checksum, application version,
Alembic revision, byte or table count, and boolean checks. It never contains a
backup path, environment value, hostname, username, URL, or credential. History
recording is deliberately best effort: an otherwise successful backup,
verification, or drill remains successful if the ledger is unavailable. Restore
history is written only to the restored database and only after quarantine hook
verification succeeds.

## Prerequisites

- Run on the Docker host as an account permitted to use Docker.
- Keep Docker Engine, Docker Compose v2, Bash, Python 3, flock, and standard GNU
  host utilities available.
- Keep the Compose **db** service running and PostgreSQL ready for backup.
- Keep the PostgreSQL image used by **db** present locally for verify and drill.
  Run docker compose pull db if it is absent.
- Use a real readable environment file and Compose file. Recovery tooling rejects
  symlinks for these files and all backup artifacts.
- Provide enough space for the dump, an off-host copy, and, during actual restore,
  a mandatory safety dump plus the temporary rollback database.

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
confirming no recovery process is active.

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

The output-directory flock prevents overlap. Alert on every nonzero exit and on
the absence of a recent completed directory. Retention jobs must ignore partial
directories and .threatlens-recovery.lock.

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

For a release-specific gate, add --expected-app-version 1.7.0.

Verification rejects malformed or unsupported manifests, partial directories,
path traversal, symlinks, non-regular files, missing archives, size differences,
and checksum differences. It then starts the configured PostgreSQL image with no
network and runs pg_restore --list against the archive.

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

1. performs manifest, checksum, and catalog verification;
2. creates a random internal Docker network with no external route;
3. starts only a temporary PostgreSQL container and data volume;
4. restores with exit-on-error and one transaction;
5. compares the restored Alembic revision with the manifest;
6. confirms application tables exist, constraints are validated, and a bounded
   application-table smoke query succeeds;
7. removes the container, volume, and network on success or failure.

No API, worker, scheduler, AI provider, SMTP connector, webhook connector, feed
fetcher, or other outbound-capable process runs in the drill network. Treat exit
6, including cleanup failure after successful checks, as a failed drill.

Retain timestamped command output, archive SHA-256, Alembic revision, table count,
operator identity, Docker and PostgreSQL versions, ticket or change identifier,
duration, and cleanup confirmation as drill evidence.

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
| preflight | Read-only. Validate the manifest envelope, supported live schema, database ownership/creation permissions, and ability to perform every action below. |
| apply | In one transaction, revoke browser sessions and API tokens, consume one-time MFA login challenges, rotate legacy JWT generations, disable all integration instances, subscriptions, and legacy webhooks, dead-letter pending outbound deliveries and events, interrupt active attempts, disable scheduled and pending report delivery, and write a system audit event. |
| verify | Read-only. Fail unless every revocation and quarantine invariant is durably true. |

The hook also receives:

- THREATLENS_RECOVERY_COMPOSE_FILES, newline-delimited in precedence order;
- THREATLENS_RECOVERY_ENV_FILE;
- THREATLENS_RECOVERY_PROJECT_NAME;
- THREATLENS_RECOVERY_MANIFEST.
- THREATLENS_RECOVERY_ARCHIVE_SHA256, the checksum already validated by the
  parent recovery process. Direct hook invocations omit it and verify the
  archive independently.

The default hook does not contact external services, log secrets, enable any
integration or schedule, clear audit evidence, or report success after partial
completion. It supports the backup schema directly because apply runs before
normal API-startup migrations. Exit zero is accepted only after the phase is
complete.

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

The command requires two independent noninteractive opt-ins:

    ./scripts/recovery/threatlens-recovery.sh \
      --env-file /opt/threatlens/.env \
      --compose-file /opt/threatlens/docker-compose.yml \
      --project-name threatlens \
      restore \
      --backup /srv/threatlens-backups/threatlens-postgresql-20260827T120000Z-1a2b3c4d \
      --confirm "RESTORE THREATLENS POSTGRESQL" \
      --acknowledge-data-loss \
      --safety-backup-dir /srv/threatlens-backups/pre-restore

The backup application version must match the deployed repository by default.
Restoring an older archive for planned forward migration additionally requires
--allow-app-version-mismatch and a hook whose preflight accepts that Alembic
revision.

The restore sequence is:

1. validate both opt-ins, hook, manifest, checksum, version, and archive catalog;
2. run hook preflight before stopping or replacing anything;
3. create a mandatory fresh online safety backup;
4. stop api, all workers, beat, and web, then prove they are stopped;
5. rename the current database for rollback and create a clean target;
6. restore transactionally without archive ownership or ACL changes;
7. run hook apply and verify;
8. clear disposable Redis without putting its password in arguments;
9. remove the rollback database only after all prior steps succeed.

The utility does not restart application services. Success prints
RESTORE_STATUS=completed_quarantined and the safety-backup path. Preserve that
output as incident evidence.

Start only the API and web UI for controlled inspection:

    docker compose up -d api web
    docker compose logs --since 10m api
    docker compose exec -T api alembic current

Confirm the expected version, migrations, encryption-key inventory, audit event,
revoked credentials, disabled integrations, quarantined outbound queues, and
quarantined schedules. An administrator must deliberately re-enable each approved
integration and schedule. Start workers and beat only after review:

    docker compose up -d worker worker-ai worker-maintenance worker-notifications beat
    docker compose ps

## Rollback and escalation

Most failures after replacement trigger an automatic rename rollback to the
untouched pre-restore database. Application services remain stopped. Errors E806,
E808, E810, and E811 state that rollback succeeded; inspect PostgreSQL and Redis,
retain logs, and make a new recovery decision before restarting.

Errors marked CRITICAL (E807, E809, E813, E814, or E815) mean both the primary
step and automatic rollback failed. Keep all application processes stopped.
Preserve Docker and PostgreSQL logs, list databases from the db container, protect
the safety archive, and escalate to a PostgreSQL operator. Do not repeatedly rerun
restore or manually drop an tl_pre_restore_* database without identifying the
last known-good state.

If the host receives INT, TERM, or HUP, or an unhandled shell failure occurs after
database replacement, the EXIT trap attempts the same rollback. Exit 130 plus an
"Emergency rollback restored" warning means the original database is back and
services remain stopped. A CRITICAL emergency-rollback warning requires the same
immediate escalation as E807/E809/E813/E814/E815. SIGKILL and host power loss
cannot run a trap; inspect both database names before taking any further action.

E812 means the restored quarantined database and cleared Redis are in place, but
the rollback database could not be deleted. Services remain stopped. Remove that
database only after confirming the safety archive and restored state.

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
