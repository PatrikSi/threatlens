# Database privileges and upgrades

The bundled Compose deployment uses three database identities. API and Celery
processes receive only the runtime connection URL. The PostgreSQL container
holds the recovery administrator credentials; a separate, short-lived `migrate`
service receives the schema owner's connection URL. Docker host administrators
can access these credentials and remain trusted operators.

| Role | Configuration | Allowed operations |
| --- | --- | --- |
| Runtime | `POSTGRES_RUNTIME_USER`, `POSTGRES_RUNTIME_PASSWORD` | Connect; schema usage; read, insert, update and delete application rows; use sequences. No schema creation, table ownership, role management, database creation, replication, or superuser privileges. |
| Migration | `POSTGRES_MIGRATION_USER`, `POSTGRES_MIGRATION_PASSWORD` | Own this application's database, public schema and application objects; apply Alembic migrations, including the trusted `pg_trgm` extension. No cluster administration or role management. |
| Recovery administrator | `POSTGRES_USER`, `POSTGRES_PASSWORD` | Provision roles, back up the database, fence access, create/rename/drop recovery databases, and reconcile interrupted restores. Present only in the database service. |

Runtime credentials still permit access to application data. This boundary
limits a compromised application process's database administration capabilities;
application authorization continues to enforce user and feed access.

## New installations

Generate configuration with `./bootstrap.sh`. It produces independent passwords
for all three roles. `docker compose up -d` initializes an empty database volume
through `scripts/database/provision-roles.sh`, runs the `migrate` service, and
starts the API only after migrations succeed. Workers start after API readiness.
`RUN_MIGRATIONS_ON_STARTUP` is false in bundled Compose.

The provisioning script revokes public database access and public schema
creation, then grants runtime data access and default privileges for future
migration-owned tables and sequences. New role names must be lowercase SQL
identifiers, no longer than 63 characters, and distinct from each other and the
recovery administrator. Existing elevated roles or inherited memberships are
refused. Passwords are passed through environment variables and standard input,
not embedded in command arguments by the provisioning script.

## Existing installations: explicit offline cutover

Changing PostgreSQL image environment variables does not modify users stored in
an existing volume. Do not replace the existing administrator password with a
new bootstrap password, rename `POSTGRES_USER`/`POSTGRES_DB`, or remove the volume.
The cutover is an operator maintenance action; ordinary restarts never transfer
existing object ownership.

1. Create and verify a backup using the currently working deployment. Retain the
   previous Compose configuration and environment file in private storage.
2. Add the four runtime/migration role variables to your deployment environment
   with independent generated passwords. Keep the initialized `POSTGRES_USER`,
   `POSTGRES_PASSWORD`, and `POSTGRES_DB`. Remove an old administrative
   `DATABASE_URL` override so the new Compose default uses runtime credentials.
3. Run the following from the repository root. Supply your normal Compose
   `--env-file`, `--file`, and `--project-name` options when they differ:

   ```bash
   python3 scripts/database/upgrade-roles.py --env-file .env --file docker-compose.yml --project-name threatlens
   ```

   The command validates the rendered role topology and existing database
   container credentials, stops the API, every recognized worker, `migrate`,
   Beat, and web, and checks that they stopped. It then creates the roles,
   transfers this database's public application objects, and grants runtime
   access in a transaction. It does not transfer objects in other databases.
   Application services remain stopped after success or failure.
4. Recreate the database container so recovery tooling sees the new role
   environment, run migrations, and restart the stack with the same options:

   ```bash
   docker compose --env-file .env --file docker-compose.yml --project-name threatlens up -d db
   docker compose --env-file .env --file docker-compose.yml --project-name threatlens run --rm migrate
   docker compose --env-file .env --file docker-compose.yml --project-name threatlens up -d
   ```

5. Check API readiness, log in, ingest a synthetic feed item, and perform a backup
   and disposable recovery drill. Review the migration service's exit status
   when API startup is blocked. Do not bypass the dependency by running schema
   upgrades from an API replica.

Provisioning can be rerun after a failure. SQL changes are transactional;
application shutdown is deliberate and is not rolled back automatically. If
cutover fails before migration, the previous administrator connection remains
available to the operator for diagnosis or returning to the previous deployment
configuration. A migration that changes the schema requires the corresponding
application rollback procedure or backup recovery; reverting environment values
alone is not a schema rollback.

## Recovery and authorization fences

The bundled recovery adapter accepts the historical single-role topology and
the new three-role topology. It validates runtime and migration URLs separately,
checks running container configuration, and refuses unrecognized data accessors
or administrator passwords exposed through split-role application services.
For split roles, the database must be owned by the configured migration role,
and both runtime and migration roles must accept logins before fencing begins.

A destructive restore stops application and migration services and disables
runtime and migration logins before replacing the database. The administrator
remains available to reconcile failures. A generated, temporary recovery role
performs restoration and quarantine. The previous database remains fenced as
the rollback copy until verification completes. Finalization assigns restored
objects to the migration owner, reinstalls runtime and future-object grants,
copies the original database ACL and database/role settings, restores the
configured login state, and tests a fresh runtime TCP connection and application
reads before removing the rollback database. Runtime credentials never become
schema owners during this process.

Only the supported bundled roles and public application schema are covered by
this adapter. Custom role memberships, additional schemas, external PostgreSQL,
proxies, or custom services require a reviewed deployment/recovery adapter.

## Validation

The disposable recovery integration test initializes separate roles, runs the
full Alembic chain as the non-superuser migration owner, seeds an administrator
through the runtime role, rejects runtime DDL, and exercises backup, verification,
recovery drill, destructive restore, quarantine, grants, ownership, and migration
access after restoration. An injected quarantine failure proves rollback restores
the original database identity and both role logins; a separate populated cutover
test proves unrelated database ownership is retained. The migration roundtrip
downgrades the new hardening revisions and reapplies them using only migration
credentials. A backup containing partially pruned permission history proves its
hidden parent and durable cleanup claim survive restore:

```bash
THREATLENS_RUN_DOCKER_RECOVERY_E2E=1 python3 -m unittest discover -s tests/recovery -p test_recovery_docker_e2e.py -v
```

It creates a unique Compose project and synthetic credentials. It does not use
the deployment's `.env` or data volumes. Set `RECOVERY_E2E_BACKEND_IMAGE` to the
locally built candidate image when validating an unreleased change.
