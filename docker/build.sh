#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: ./docker/build.sh [all|backend|web] [--pull] [--no-cache] [--platform PLATFORM]

Build both custom images (default), or just the backend or web image.
The backend image is shared by the API, every worker, and Beat.

  ./docker/build.sh
  ./docker/build.sh backend
  ./docker/build.sh web --no-cache
  ./docker/build.sh all --pull --platform linux/amd64

Exported environment overrides (the script does not load .env):
  THREATLENS_DEV_IMAGE_TAG  Local image tag (default: dev)
  THREATLENS_BUILD_VERSION App version (default: repository VERSION)
  BUILD_DATE              OCI creation time (default: current UTC time)
  VCS_REF                 OCI revision (default: checked-out Git commit)
  WEB_VITE_API_BASE_URL    Web API path (default: /api/v1)

Images: threatlens-backend:<tag> and threatlens-web:<tag>.
Use docker-compose.build.yml to run these images with Docker Compose.
USAGE
}

target=all
if [[ $# -gt 0 && "$1" != -* ]]; then
  target=$1
  shift
fi
case "$target" in
  all|backend|web) ;;
  *) printf 'Unknown image: %s. Choose all, backend, or web.\n' "$target" >&2; exit 2 ;;
esac

build_options=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --pull|--no-cache) build_options+=("$1"); shift ;;
    --platform)
      if [[ $# -lt 2 || -z "$2" || "$2" == -* ]]; then
        echo '--platform requires a platform, for example linux/amd64.' >&2
        exit 2
      fi
      build_options+=("--platform" "$2")
      shift 2
      ;;
    *) printf 'Unknown option: %s. Run with --help for usage.\n' "$1" >&2; exit 2 ;;
  esac
done

if ! command -v docker >/dev/null 2>&1; then
  echo 'Docker is required to build ThreatLens images.' >&2
  exit 1
fi

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
image_tag="${THREATLENS_DEV_IMAGE_TAG:-dev}"
if ! [[ "$image_tag" =~ ^[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,127}$ ]]; then
  echo 'THREATLENS_DEV_IMAGE_TAG must be a valid Docker image tag (up to 128 characters).' >&2
  exit 2
fi
app_version="${THREATLENS_BUILD_VERSION:-$(cat "$repo_root/VERSION")}"
build_date="${BUILD_DATE:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"
vcs_ref="${VCS_REF:-$(git -C "$repo_root" rev-parse HEAD 2>/dev/null || printf unknown)}"

build_image() {
  local name=$1
  local image="threatlens-${name}:${image_tag}"
  local image_options=()
  if [[ "$name" == web ]]; then
    image_options+=("--build-arg" "VITE_API_BASE_URL=${WEB_VITE_API_BASE_URL:-/api/v1}")
  fi
  printf 'Building %s\n' "$image"
  docker build \
    --file "$repo_root/docker/$name.Dockerfile" \
    --tag "$image" \
    --build-arg "APP_VERSION=$app_version" \
    --build-arg "BUILD_DATE=$build_date" \
    --build-arg "VCS_REF=$vcs_ref" \
    "${image_options[@]}" \
    "${build_options[@]}" \
    "$repo_root/$name"
}

if [[ "$target" == all ]]; then
  build_image backend
  build_image web
else
  build_image "$target"
fi
