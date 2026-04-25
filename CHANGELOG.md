# Changelog

All notable changes to ThreatLens should be documented in this file.

The format is based on Keep a Changelog. Public releases should be cut as immutable tags in `vX.Y.Z` format. `main` tracks unreleased development between tags. Until the first public tag exists, this `Unreleased` section documents the current public state and the deployed commit SHA plus image digest should be treated as the operator's upgrade anchor.

## [Unreleased]

### Release Contract

- Current checked-in OpenAPI contract anchor: `openapi-sha256:65fa714280c8494ed06be4433ae2d1303f6a309eb0b1bfc88707d93300631646`
- Public releases should record that contract anchor alongside the immutable `vX.Y.Z` tag and published image digests

### Added

- OSS governance documents: `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, and `CHANGELOG.md`
- GitHub issue templates, pull request template, and CI coverage for backend tests plus frontend lint, tests, and build
- Bundled license-text artifacts under `docs/licenses/`
- Reproducible runtime dependency inventories under `docs/reference/`
- Release-process documentation for contract, compliance, and support updates
- Backend `ALLOWED_HOSTS` enforcement, an `EXPOSE_OPENAPI_SCHEMA_IN_PRODUCTION` switch, and worker queue coverage in readiness checks

### Changed

- Standardized shipped web/runtime docs on the published API base path `/api/v1`
- Expanded auth documentation for cookie JWT sessions, CSRF handling, and personal API token behavior
- Hardened notification webhook egress defaults so admin-managed destinations require an allowlist unless `NOTIFICATION_WEBHOOK_ALLOW_ADMIN_UNRESTRICTED=true` is explicitly enabled
- Refined AI task reconciliation and provider retry behavior so queued backlog is not falsely marked lost and terminal provider/configuration failures do not retry
- Preserved feed retry attempts through dispatch-claim backoff so transient fetch failures can use Celery retries
- Improved browser resilience for route render failures, logout failures, password-change session invalidation, and non-JSON or empty API responses
- Refreshed third-party notices for bundled OFL fonts and `psycopg[binary]` redistribution guidance
- Added shipped OS package notice artifacts and a discoverable backend legal bundle under `/usr/share/doc/threatlens/`
- Documented trust boundaries and outbound data flows more explicitly
- Added a shipped frontend Alpine OS legal bundle and aligned the checked-in compliance artifacts with the actual built web image contents
- Improved backend machine-readable dependency metadata by preferring published `License-Expression` values over avoidable `Unknown` placeholders
- Generated backend runtime locks from runtime dependency roots instead of every installed package, and pinned CI/test service images by digest
- Tightened release, support, and governance language so the public repo can be used responsibly before the first immutable tag exists
- Added a release-time guard command so `CHANGELOG.md` can be checked against the checked-in OpenAPI contract anchor before tagging

### Security

- Switched new password hashes to `bcrypt_sha256` and rejected overlong passwords against legacy bcrypt hashes to avoid bcrypt truncation aliases
- Rejected unsafe production credentialed CORS origins and unsafe production Host header allowlists
- Updated the frontend PostCSS dev dependency to a non-vulnerable version
