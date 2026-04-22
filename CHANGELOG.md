# Changelog

All notable changes to ThreatLens should be documented in this file.

The format is based on Keep a Changelog. Public releases should be cut as immutable tags in `vX.Y.Z` format. `main` tracks unreleased development between tags. Only the latest published tag is considered a supported release line; older tags remain historical references for upgrade context.

## [Unreleased]

### Added

- OSS governance documents: `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, and `CHANGELOG.md`
- Bundled license-text artifacts under `docs/licenses/`
- Reproducible runtime dependency inventories under `docs/reference/`
- Release-process documentation for contract, compliance, and support updates

### Changed

- Standardized shipped web/runtime docs on the published API base path `/api/v1`
- Expanded auth documentation for cookie JWT sessions, CSRF handling, and personal API token behavior
- Refreshed third-party notices for bundled OFL fonts and `psycopg[binary]` redistribution guidance
- Documented trust boundaries and outbound data flows more explicitly
- Replaced local-only maintainer contacts with explicit non-routable placeholders plus a public-release gating policy
