#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: ./bootstrap.sh [--force] [output-file]
       ./bootstrap.sh --print-compose-env

Generate a local .env file with fresh random secrets for the default
Docker Compose stack. The default output file is .env in the current directory.

Use --print-compose-env to print pasteable YAML environment mappings for
docker-compose.yml instead of writing a file. The legacy --print-portainer-env
flag is still accepted as an alias.

Environment overrides:
  ADMIN_EMAIL      Admin email to write into the generated output.
  ADMIN_PASSWORD   Admin password to write into the generated output.
USAGE
}

force=false
output_file=""
print_compose_env=false

while [ "$#" -gt 0 ]; do
  case "$1" in
    --force)
      force=true
      shift
      ;;
    --print-compose-env|--print-portainer-env)
      print_compose_env=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      if [ -n "$output_file" ]; then
        echo "Unexpected argument: $1" >&2
        usage >&2
        exit 2
      fi
      output_file="$1"
      shift
      ;;
  esac
done

if [ "$print_compose_env" = "true" ] && [ -n "$output_file" ]; then
  echo "--print-compose-env does not take an output file." >&2
  usage >&2
  exit 2
fi

if [ "$print_compose_env" = "true" ] && [ "$force" = "true" ]; then
  echo "--force is only used when writing a local .env file." >&2
  usage >&2
  exit 2
fi

if [ -z "$output_file" ] && [ "$print_compose_env" != "true" ]; then
  output_file=".env"
fi

if [ -n "$output_file" ] && [ -e "$output_file" ] && [ "$force" != "true" ]; then
  echo "$output_file already exists. Use --force to replace it." >&2
  exit 1
fi

random_value() {
  local length="${1:-48}"
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -base64 "$length" | tr '+/' '-_' | tr -d '=\n' | cut -c "1-$length"
    return
  fi

  if command -v python3 >/dev/null 2>&1; then
    python3 - "$length" <<'PY'
import secrets
import sys

length = int(sys.argv[1])
print(secrets.token_urlsafe(length)[:length])
PY
    return
  fi

  echo "Unable to generate secrets: install openssl or python3." >&2
  exit 1
}

postgres_db="${POSTGRES_DB:-threatlens}"
postgres_user="${POSTGRES_USER:-threatlens}"
postgres_password="$(random_value 40)"
redis_password="$(random_value 40)"
jwt_secret="$(random_value 64)"
app_data_encryption_key="$(random_value 64)"
admin_email="${ADMIN_EMAIL:-admin@example.com}"
admin_password="${ADMIN_PASSWORD:-$(random_value 24)}"
database_url="postgresql+psycopg://${postgres_user}:${postgres_password}@db:5432/${postgres_db}"
redis_url="redis://:${redis_password}@redis:6379/0"
compose_project_name="${COMPOSE_PROJECT_NAME:-$(basename "$PWD" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9_-]//g')}"
if [ -z "$compose_project_name" ]; then
  compose_project_name="threatlens"
fi
postgres_volume_name="${compose_project_name}_postgres_data"

render_env_file_block() {
  cat <<EOF
POSTGRES_DB=$postgres_db
POSTGRES_USER=$postgres_user
POSTGRES_PASSWORD=$postgres_password
REDIS_PASSWORD=$redis_password
DATABASE_URL=
REDIS_URL=
JWT_SECRET=$jwt_secret
APP_DATA_ENCRYPTION_KEY=$app_data_encryption_key
APP_DATA_ENCRYPTION_PREVIOUS_KEYS=
REQUIRE_EXPLICIT_DATA_ENCRYPTION_KEY=true
JWT_EXPIRES_MINUTES=1440
ADMIN_EMAIL=$admin_email
ADMIN_PASSWORD=$admin_password
SEED_ADMIN_ON_STARTUP=true
SEED_ADMIN_FORCE_ROLE=false
SEED_ADMIN_REACTIVATE_EXISTING=false
SEED_ADMIN_RESET_PASSWORD_ON_STARTUP=false
APP_ENV=development
AUTH_COOKIE_SECURE=false
AUTH_COOKIE_SAMESITE=lax
AUTH_REQUIRE_CSRF=true
AUTH_COOKIE_NAME=threatlens_session
AUTH_CSRF_COOKIE_NAME=threatlens_csrf
AUTH_CSRF_HEADER_NAME=x-csrf-token
RUN_MIGRATIONS_ON_STARTUP=true
ALLOW_SELF_REGISTRATION=false
ALLOW_LEGACY_UNSCOPED_TOKENS=false
AI_ENABLED=false
AI_API_KEY=
ALLOW_PRIVATE_NETWORK_FETCH=false
ALLOW_PRIVATE_NETWORK_AI=false
ALLOW_PRIVATE_NETWORK_WEBHOOKS=false
ALLOW_PRIVATE_NETWORK_OIDC=false
ALLOW_INSECURE_HTTP_OIDC=false
OIDC_CALLBACK_PATH=/api/v1/auth/oidc/callback
WEB_VITE_API_BASE_URL=/api/v1
THREATLENS_WEB_PORT=3000
THREATLENS_IMAGE_TAG=latest
THREATLENS_CSP_CONNECT_SRC="'self'"
THREATLENS_CSP_FRAME_SRC="'self'"
LOG_LEVEL=INFO
EOF
}

yaml_quote() {
  printf "'"
  printf "%s" "$1" | sed "s/'/''/g"
  printf "'"
}

render_yaml_entry() {
  local key="$1"
  local value="$2"
  printf "  %s: " "$key"
  yaml_quote "$value"
  printf "\n"
}

render_compose_env_mapping() {
  cat <<EOF
x-db-environment: &db-environment
EOF
  render_yaml_entry "POSTGRES_DB" "$postgres_db"
  render_yaml_entry "POSTGRES_USER" "$postgres_user"
  render_yaml_entry "POSTGRES_PASSWORD" "$postgres_password"
  cat <<EOF

x-redis-environment: &redis-environment
EOF
  render_yaml_entry "REDIS_PASSWORD" "$redis_password"
  cat <<EOF

x-backend-environment: &backend-environment
  <<: [*db-environment, *redis-environment]
EOF
  render_yaml_entry "APP_ENV" "development"
  render_yaml_entry "DATABASE_URL" "$database_url"
  render_yaml_entry "REDIS_URL" "$redis_url"
  render_yaml_entry "JWT_SECRET" "$jwt_secret"
  render_yaml_entry "APP_DATA_ENCRYPTION_KEY" "$app_data_encryption_key"
  render_yaml_entry "APP_DATA_ENCRYPTION_PREVIOUS_KEYS" ""
  render_yaml_entry "REQUIRE_EXPLICIT_DATA_ENCRYPTION_KEY" "true"
  render_yaml_entry "JWT_EXPIRES_MINUTES" "1440"
  render_yaml_entry "ADMIN_EMAIL" "$admin_email"
  render_yaml_entry "ADMIN_PASSWORD" "$admin_password"
  render_yaml_entry "ALLOW_SELF_REGISTRATION" "false"
  render_yaml_entry "ALLOW_LEGACY_UNSCOPED_TOKENS" "false"
  render_yaml_entry "DEFAULT_API_TOKEN_EXPIRY_DAYS" "90"
  render_yaml_entry "AI_ENABLED" "false"
  render_yaml_entry "AI_API_KEY" ""
  render_yaml_entry "EXPOSE_API_DOCS_IN_PRODUCTION" "false"
  render_yaml_entry "EXPOSE_OPENAPI_SCHEMA_IN_PRODUCTION" "true"
  render_yaml_entry "FEED_MAX_BYTES" "2000000"
  render_yaml_entry "ALLOW_PRIVATE_NETWORK_FETCH" "false"
  render_yaml_entry "ALLOW_PRIVATE_NETWORK_AI" "false"
  render_yaml_entry "ALLOW_PRIVATE_NETWORK_WEBHOOKS" "false"
  render_yaml_entry "ALLOW_PRIVATE_NETWORK_OIDC" "false"
  render_yaml_entry "ALLOW_INSECURE_HTTP_OIDC" "false"
  render_yaml_entry "OUTBOUND_MAX_REDIRECTS" "5"
  render_yaml_entry "AUTH_LOGIN_MAX_ATTEMPTS" "8"
  render_yaml_entry "AUTH_LOGIN_WINDOW_SECONDS" "300"
  render_yaml_entry "AUTH_LOGIN_LOCKOUT_SECONDS" "900"
  render_yaml_entry "API_TOKEN_LAST_USED_UPDATE_INTERVAL_SECONDS" "300"
  render_yaml_entry "OIDC_TRANSACTION_COOKIE_NAME" "threatlens_oidc_transaction"
  render_yaml_entry "OIDC_TRANSACTION_TTL_SECONDS" "600"
  render_yaml_entry "OIDC_CALLBACK_PATH" "/api/v1/auth/oidc/callback"
  render_yaml_entry "OIDC_METADATA_CACHE_SECONDS" "300"
  render_yaml_entry "OIDC_CONNECT_TIMEOUT_SECONDS" "5"
  render_yaml_entry "OIDC_READ_TIMEOUT_SECONDS" "10"
  render_yaml_entry "OIDC_MAX_RESPONSE_BYTES" "1000000"
  render_yaml_entry "CORS_ORIGINS" "http://localhost:3000,http://127.0.0.1:3000"
  render_yaml_entry "TRUSTED_PROXY_CIDRS" ""
  render_yaml_entry "ALLOWED_HOSTS" "api,localhost,127.0.0.1,::1"
  render_yaml_entry "AUTH_COOKIE_NAME" "threatlens_session"
  render_yaml_entry "AUTH_COOKIE_SECURE" "false"
  render_yaml_entry "AUTH_COOKIE_SAMESITE" "lax"
  render_yaml_entry "AUTH_CSRF_COOKIE_NAME" "threatlens_csrf"
  render_yaml_entry "AUTH_CSRF_HEADER_NAME" "x-csrf-token"
  render_yaml_entry "AUTH_REQUIRE_CSRF" "true"
  render_yaml_entry "RUN_MIGRATIONS_ON_STARTUP" "true"
  render_yaml_entry "SEED_ADMIN_ON_STARTUP" "true"
  render_yaml_entry "PROBE_FEED_METADATA_ON_CREATE" "false"
  render_yaml_entry "PROBE_FEED_METADATA_ON_IMPORT" "false"
  render_yaml_entry "MAX_METADATA_BACKFILL_TASKS_PER_REQUEST" "100"
  render_yaml_entry "DISPATCH_DUE_FEEDS_BATCH_SIZE" "500"
  render_yaml_entry "DISPATCH_UNCLASSIFIED_ITEMS_BATCH_SIZE" "200"
  render_yaml_entry "DISPATCH_ITEMS_MISSING_IOCS_BATCH_SIZE" "200"
  render_yaml_entry "DISPATCH_ITEMS_MISSING_AI_ENRICHMENT_BATCH_SIZE" "200"
  render_yaml_entry "DISPATCH_ITEMS_FAILED_AI_ENRICHMENT_AFTER_SECONDS" "3600"
  render_yaml_entry "AI_AUTO_ENRICH_NEW_ITEM_MAX_AGE_HOURS" "24"
  render_yaml_entry "AI_DAILY_BRIEF_SOURCE_AUDIT_LIMIT" "500"
  render_yaml_entry "DISPATCH_FEED_METADATA_SCAN_LIMIT" "250"
  render_yaml_entry "DISPATCH_FEED_METADATA_QUEUE_LIMIT" "50"
  render_yaml_entry "DISPATCH_AI_REPROCESS_BATCH_SIZE" "100"
  render_yaml_entry "ALERT_MATCHES_KEYWORD_CAP" "512"
  render_yaml_entry "STATS_TOP_DOMAINS_LIMIT" "10"
  render_yaml_entry "SEED_ADMIN_FORCE_ROLE" "false"
  render_yaml_entry "SEED_ADMIN_REACTIVATE_EXISTING" "false"
  render_yaml_entry "SEED_ADMIN_RESET_PASSWORD_ON_STARTUP" "false"
  render_yaml_entry "LOG_LEVEL" "INFO"
  render_yaml_entry "HEALTH_WORKER_PING_TIMEOUT_SECONDS" "1.0"
  render_yaml_entry "BEAT_HEARTBEAT_KEY" "threatlens:beat:heartbeat"
  render_yaml_entry "BEAT_HEARTBEAT_TTL_SECONDS" "180"
  render_yaml_entry "BEAT_HEARTBEAT_STALE_AFTER_SECONDS" "180"
  render_yaml_entry "BEAT_HEARTBEAT_INTERVAL_SECONDS" "60"
}

if [ "$print_compose_env" = "true" ]; then
  render_compose_env_mapping
  exit 0
fi

umask 077
cat > "$output_file" <<EOF
# Generated by bootstrap.sh.
# These values are intended for a local HTTP deployment at http://localhost:3000.
# Review .env.example before using this file for an internet-facing deployment.
$(render_env_file_block)
EOF
chmod 600 "$output_file"

cat <<EOF
Created $output_file

Admin login:
  Email:    $admin_email
  Password: $admin_password

Start ThreatLens with:
  docker compose pull
  docker compose up -d

After the first admin account exists, you can set SEED_ADMIN_ON_STARTUP=false in $output_file.
EOF

if command -v docker >/dev/null 2>&1 && docker volume inspect "$postgres_volume_name" >/dev/null 2>&1; then
  cat <<EOF

Warning: Docker volume $postgres_volume_name already exists.
If this is from a failed first startup and the API logs say the PostgreSQL role
"threatlens" does not exist, reset the local database volume with:
  docker compose down -v
  docker compose up -d
EOF
fi
