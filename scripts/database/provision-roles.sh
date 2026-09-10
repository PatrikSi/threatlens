#!/usr/bin/env bash
# Runs inside the PostgreSQL container on an empty volume, or during an explicit
# offline upgrade. Never source deployment files or put passwords in arguments.
set -euo pipefail
: "${POSTGRES_DB:?required}" "${POSTGRES_USER:?required}"
: "${POSTGRES_RUNTIME_USER:?required}" "${POSTGRES_RUNTIME_PASSWORD:?required}"
: "${POSTGRES_MIGRATION_USER:?required}" "${POSTGRES_MIGRATION_PASSWORD:?required}"

psql --no-psqlrc --set=ON_ERROR_STOP=1 --quiet \
  --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
\getenv admin_role POSTGRES_USER
\getenv app_role POSTGRES_RUNTIME_USER
\getenv app_password POSTGRES_RUNTIME_PASSWORD
\getenv owner_role POSTGRES_MIGRATION_USER
\getenv owner_password POSTGRES_MIGRATION_PASSWORD
BEGIN;
SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '60s';
SELECT pg_advisory_xact_lock(821846002109117::bigint);
SELECT set_config('threatlens.runtime_role', :'app_role', true);
SELECT set_config('threatlens.owner_role', :'owner_role', true);
DO $validate$
DECLARE
  runtime_name text := current_setting('threatlens.runtime_role');
  owner_name text := current_setting('threatlens.owner_role');
BEGIN
  IF runtime_name = owner_name OR current_user IN (runtime_name, owner_name)
     OR runtime_name !~ '^[a-z_][a-z0-9_]{0,62}$'
     OR owner_name !~ '^[a-z_][a-z0-9_]{0,62}$' THEN
    RAISE EXCEPTION 'runtime, migration and recovery roles must be distinct supported identifiers';
  END IF;
  IF EXISTS (
    SELECT 1 FROM pg_roles WHERE rolname IN (runtime_name, owner_name)
      AND (rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls OR NOT rolcanlogin)
  ) OR EXISTS (
    SELECT 1 FROM pg_auth_members AS membership JOIN pg_roles AS role ON role.oid = membership.member
    WHERE role.rolname IN (runtime_name, owner_name)
  ) THEN
    RAISE EXCEPTION 'existing runtime/migration roles have unexpected privileges or memberships';
  END IF;
  IF EXISTS (
    SELECT 1 FROM pg_class WHERE relowner = (SELECT oid FROM pg_roles WHERE rolname = runtime_name)
  ) OR EXISTS (
    SELECT 1 FROM pg_namespace WHERE nspowner = (SELECT oid FROM pg_roles WHERE rolname = runtime_name)
  ) THEN
    RAISE EXCEPTION 'runtime role must not own database objects';
  END IF;
  IF (SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = current_database())
      NOT IN (current_user, owner_name) THEN
    RAISE EXCEPTION 'database ownership is outside the supported upgrade topology';
  END IF;
END
$validate$;
SELECT format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS', name)
FROM (VALUES (:'app_role'), (:'owner_role')) AS names(name)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = name) \gexec
SELECT format('ALTER ROLE %I PASSWORD %L', :'app_role', :'app_password') \gexec
SELECT format('ALTER ROLE %I PASSWORD %L', :'owner_role', :'owner_password') \gexec
-- Restrict the database before changing ownership. Existing data is retained.
SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', current_database()) \gexec
SELECT format('ALTER DATABASE %I OWNER TO %I', current_database(), :'owner_role') \gexec
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SELECT format('ALTER SCHEMA public OWNER TO %I', :'owner_role') \gexec
-- REASSIGN OWNED also changes shared objects in other databases. Transfer only
-- this application's public objects, excluding trusted extension internals.
SELECT format('ALTER %s %I.%I OWNER TO %I',
  CASE relation.relkind WHEN 'S' THEN 'SEQUENCE' WHEN 'v' THEN 'VIEW'
       WHEN 'm' THEN 'MATERIALIZED VIEW' ELSE 'TABLE' END,
  namespace.nspname, relation.relname, :'owner_role')
FROM pg_class AS relation JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
WHERE namespace.nspname = 'public' AND relation.relkind IN ('r', 'p', 'S', 'v', 'm', 'f')
  AND relation.relowner = (SELECT oid FROM pg_roles WHERE rolname = :'admin_role')
  AND NOT EXISTS (SELECT 1 FROM pg_depend WHERE classid = 'pg_class'::regclass
                  AND objid = relation.oid AND deptype = 'e') \gexec
SELECT format('ALTER ROUTINE %s OWNER TO %I', routine.oid::regprocedure, :'owner_role')
FROM pg_proc AS routine JOIN pg_namespace AS namespace ON namespace.oid = routine.pronamespace
WHERE namespace.nspname = 'public'
  AND routine.proowner = (SELECT oid FROM pg_roles WHERE rolname = :'admin_role')
  AND NOT EXISTS (SELECT 1 FROM pg_depend WHERE classid = 'pg_proc'::regclass
                  AND objid = routine.oid AND deptype = 'e') \gexec
SELECT format('ALTER TYPE %I.%I OWNER TO %I', namespace.nspname, type.typname, :'owner_role')
FROM pg_type AS type JOIN pg_namespace AS namespace ON namespace.oid = type.typnamespace
WHERE namespace.nspname = 'public' AND type.typtype IN ('e', 'd')
  AND type.typowner = (SELECT oid FROM pg_roles WHERE rolname = :'admin_role') \gexec
SELECT format('REVOKE ALL ON DATABASE %I FROM %I', current_database(), :'app_role') \gexec
SELECT format('REVOKE ALL ON SCHEMA public FROM %I', :'app_role') \gexec
SELECT format('REVOKE ALL ON ALL TABLES IN SCHEMA public FROM %I', :'app_role') \gexec
SELECT format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM %I', :'app_role') \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), :'app_role') \gexec
SELECT format('GRANT USAGE ON SCHEMA public TO %I', :'app_role') \gexec
SELECT format('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO %I', :'app_role') \gexec
SELECT format('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO %I', :'app_role') \gexec
SELECT format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I',
              :'owner_role', :'app_role') \gexec
SELECT format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO %I',
              :'owner_role', :'app_role') \gexec
COMMIT;
SQL
