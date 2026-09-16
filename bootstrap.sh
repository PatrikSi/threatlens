#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: ./bootstrap.sh [--force] [output-file]
       ./bootstrap.sh --print-compose-env

Generate a complete local .env from the matching .env.example, with fresh random
secrets and first-run settings for the default Docker Compose stack. The default
output file is .env in the current directory.

Use --print-compose-env to print pasteable YAML environment mappings for
docker-compose.yml instead of writing a file. The legacy --print-portainer-env
flag is still accepted as an alias.

For Kubernetes, generate and edit the environment first, then export one
service's resolved values with scripts/export_kubernetes_secret.py. A Compose
.env file is not a Kubernetes Secret; see docs/reference/configuration.md.

Environment overrides:
  ADMIN_EMAIL      Admin email to write into the generated output.
  ADMIN_PASSWORD   Admin password to write into the generated output.
  POSTGRES_DB, POSTGRES_USER, POSTGRES_RUNTIME_USER, POSTGRES_MIGRATION_USER
                  Database and role names for a new installation.

--force replaces every generated secret. Do not use it to upgrade an existing
installation; retain its database credentials and application encryption keys.
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
    -*)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
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

if [ -n "$output_file" ]; then
  if [ -L "$output_file" ] || { [ -e "$output_file" ] && [ ! -f "$output_file" ]; }; then
    echo "Refusing to replace a symlink or non-regular file: $output_file" >&2
    exit 1
  fi
  if [ -e "$output_file" ] && [ "$force" != "true" ]; then
    echo "$output_file already exists. Retain it for upgrades; --force replaces all secrets." >&2
    exit 1
  fi
fi

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
environment_template="$script_directory/.env.example"
if [ ! -f "$environment_template" ] || [ ! -r "$environment_template" ]; then
  echo "Unable to read .env.example. Run bootstrap.sh from a complete matching ThreatLens checkout." >&2
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
postgres_runtime_user="${POSTGRES_RUNTIME_USER:-threatlens_runtime}"
postgres_runtime_password="$(random_value 40)"
postgres_migration_user="${POSTGRES_MIGRATION_USER:-threatlens_migration}"
postgres_migration_password="$(random_value 40)"
redis_password="$(random_value 40)"
jwt_secret="$(random_value 64)"
app_data_encryption_key="$(random_value 64)"
admin_email="${ADMIN_EMAIL:-admin@example.com}"
admin_password="${ADMIN_PASSWORD:-$(random_value 24)}"
compose_project_name="${COMPOSE_PROJECT_NAME:-$(basename "$PWD" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9_-]//g')}"
if [ -z "$compose_project_name" ]; then
  compose_project_name="threatlens"
fi
postgres_volume_name="${compose_project_name}_postgres_data"

validate_single_line() {
  if [[ "$2" == *$'\n'* || "$2" == *$'\r'* ]]; then
    echo "$1 must be a single-line value." >&2
    exit 2
  fi
}

validate_single_line ADMIN_EMAIL "$admin_email"
validate_single_line ADMIN_PASSWORD "$admin_password"
validate_single_line POSTGRES_USER "$postgres_user"

admin_email="${admin_email#"${admin_email%%[![:space:]]*}"}"
admin_email="${admin_email%"${admin_email##*[![:space:]]}"}"
valid_admin_email() {
  if [[ ! "$admin_email" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]]; then
    return 1
  fi
  local local_part="${admin_email%@*}"
  local domain="${admin_email#*@}"
  local label
  local -a labels
  # The login API rejects dot-boundary errors and invalid domain labels.
  # Catch these before seeding an administrator who cannot sign in.
  if [[ "$local_part" == .* || "$local_part" == *. || "$local_part" == *..* ||
        "$domain" == .* || "$domain" == *. || "$domain" == *..* ]]; then
    return 1
  fi
  IFS=. read -r -a labels <<< "$domain"
  for label in "${labels[@]}"; do
    if [[ "${#label}" -gt 63 || ! "$label" =~ ^[[:alnum:]]([[:alnum:]-]*[[:alnum:]])?$ ]]; then
      return 1
    fi
  done
}
if ! valid_admin_email; then
  echo "ADMIN_EMAIL must be an email address, such as admin@example.com." >&2
  exit 2
fi
if [ "${#admin_password}" -gt 256 ]; then
  echo "ADMIN_PASSWORD must be at most 256 characters, matching the login form limit." >&2
  exit 2
fi
normalized_password="${admin_password#"${admin_password%%[![:space:]]*}"}"
normalized_password="${normalized_password%"${normalized_password##*[![:space:]]}"}"
normalized_password="$(printf '%s' "$normalized_password" | tr '[:upper:]' '[:lower:]')"
case "$normalized_password" in
  admin123|replace-with*|change-me*|changeme*|placeholder*|example-*|your-*)
    echo "ADMIN_PASSWORD must not use a default or placeholder value when creating the first administrator." >&2
    exit 2
    ;;
esac

if [[ ! "$postgres_db" =~ ^[a-zA-Z_][a-zA-Z0-9_-]{0,62}$ ]]; then
  echo "POSTGRES_DB must be a URL-safe database name of at most 63 characters (letters, digits, _ or -)." >&2
  exit 2
fi
if [[ ! "$postgres_user" =~ ^[a-zA-Z_][a-zA-Z0-9_-]{0,62}$ || "$postgres_user" == pg_* ]]; then
  echo "POSTGRES_USER must be a SQL identifier of at most 63 characters (letters, digits, _ or -), without the reserved pg_ prefix." >&2
  exit 2
fi
for role_variable in postgres_runtime_user postgres_migration_user; do
  role_name="${!role_variable}"
  if [[ ! "$role_name" =~ ^[a-z_][a-z0-9_]{0,62}$ || "$role_name" == pg_* ]]; then
    role_label="$(printf '%s' "$role_variable" | tr '[:lower:]' '[:upper:]')"
    echo "$role_label must be a lowercase SQL identifier of at most 63 characters, without the reserved pg_ prefix." >&2
    exit 2
  fi
done
if [[ "$postgres_runtime_user" == "$postgres_migration_user" ||
      "$postgres_user" == "$postgres_runtime_user" ||
      "$postgres_user" == "$postgres_migration_user" ]]; then
  echo "POSTGRES_USER, POSTGRES_RUNTIME_USER and POSTGRES_MIGRATION_USER must be distinct." >&2
  exit 2
fi

dotenv_quote() {
  # Compose interpolates dollar signs in double-quoted dotenv values. Escape
  # these separately from backslashes and quotes to preserve chosen passwords.
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  value="${value//\$/\$\$}"
  printf '"%s"' "$value"
}

render_env_file_block() {
  local line key
  # Read data, never source it. Keeping the complete template makes new
  # settings and their comments available without another copied defaults list.
  while IFS= read -r line || [ -n "$line" ]; do
    if [[ "$line" =~ ^([A-Z][A-Z0-9_]*)= ]]; then
      key="${BASH_REMATCH[1]}"
      case "$key" in
        POSTGRES_DB) line="$key=$postgres_db" ;;
        POSTGRES_USER) line="$key=$(dotenv_quote "$postgres_user")" ;;
        POSTGRES_PASSWORD) line="$key=$postgres_password" ;;
        POSTGRES_RUNTIME_USER) line="$key=$postgres_runtime_user" ;;
        POSTGRES_RUNTIME_PASSWORD) line="$key=$postgres_runtime_password" ;;
        POSTGRES_MIGRATION_USER) line="$key=$postgres_migration_user" ;;
        POSTGRES_MIGRATION_PASSWORD) line="$key=$postgres_migration_password" ;;
        REDIS_PASSWORD) line="$key=$redis_password" ;;
        JWT_SECRET) line="$key=$jwt_secret" ;;
        APP_DATA_ENCRYPTION_KEY) line="$key=$app_data_encryption_key" ;;
        ADMIN_EMAIL) line="$key=$(dotenv_quote "$admin_email")" ;;
        ADMIN_PASSWORD) line="$key=$(dotenv_quote "$admin_password")" ;;
        APP_ENV) line="$key=development" ;;
        AUTH_COOKIE_SECURE) line="$key=false" ;;
        SEED_ADMIN_ON_STARTUP) line="$key=true" ;;
      esac
    fi
    printf '%s\n' "$line"
  done < "$environment_template"
}

if [ "$print_compose_env" = "true" ]; then
  if ! command -v python3 >/dev/null 2>&1 || ! command -v docker >/dev/null 2>&1; then
    echo "--print-compose-env requires Python 3 and Docker Compose v2; no running Docker daemon is needed." >&2
    exit 1
  fi
  umask 077
  temporary_file="$(mktemp "${TMPDIR:-/tmp}/threatlens-bootstrap.XXXXXX")"
  trap 'rm -f -- "$temporary_file"' EXIT
  render_env_file_block > "$temporary_file"
  python3 "$script_directory/scripts/bootstrap_compose_env.py" \
    "$temporary_file" "$script_directory/docker-compose.yml"
  exit 0
fi

umask 077
temporary_file="$(mktemp "${output_file}.tmp.XXXXXX")"
trap 'rm -f -- "$temporary_file"' EXIT
{
  cat <<'EOF'
# Generated by bootstrap.sh.
# These values are intended for a local HTTP deployment at http://localhost:3000.
# All settings and comments below come from .env.example, with generated secrets
# and local first-run overrides. Review them before internet-facing deployment.
EOF
  render_env_file_block
} > "$temporary_file"
if [ "$force" = "true" ]; then
  mv -f -- "$temporary_file" "$output_file"
else
  # A concurrent bootstrap must not replace credentials already written by
  # another process after the existence check above.
  ln -- "$temporary_file" "$output_file"
fi

compose_command="docker compose"
if [ "$output_file" != ".env" ]; then
  printf -v quoted_output_file '%q' "$output_file"
  compose_command+=" --env-file $quoted_output_file"
fi

cat <<EOF
Created $output_file

Admin login:
  Email:    $admin_email
  Password: $admin_password

Start ThreatLens with:
  $compose_command pull
  $compose_command up -d --wait

After the first admin account exists, you can set SEED_ADMIN_ON_STARTUP=false in $output_file.
EOF

if command -v docker >/dev/null 2>&1 && docker volume inspect "$postgres_volume_name" >/dev/null 2>&1; then
  cat <<EOF

Docker volume $postgres_volume_name already exists.
Generated passwords do not change credentials stored in an existing volume.
Keep the existing administrator password and follow the database role cutover
instructions in docs/pages/database-privileges.md. Do not delete the volume.
EOF
fi
