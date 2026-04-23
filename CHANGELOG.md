# Changelog

All notable changes to ThreatLens should be documented in this file.

The format is based on Keep a Changelog. Public releases should be cut as immutable tags in `vX.Y.Z` format. `main` tracks unreleased development between tags. Until the first public tag exists, this `Unreleased` section documents the current public state and the deployed commit SHA plus image digest should be treated as the operator's upgrade anchor.

## [Unreleased]

### Release Contract

- Current checked-in OpenAPI contract anchor: `openapi-sha256:57889821ca2b4d37f7696600fc30057ab5614353f3643584c8ffad698887a227`
- Public releases should record that contract anchor alongside the immutable `vX.Y.Z` tag and published image digests

### Added

- OSS governance documents: `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, and `CHANGELOG.md`
- Bundled license-text artifacts under `docs/licenses/`
- Reproducible runtime dependency inventories under `docs/reference/`
- Release-process documentation for contract, compliance, and support updates

### Changed

- Standardized shipped web/runtime docs on the published API base path `/api/v1`
- Expanded auth documentation for cookie JWT sessions, CSRF handling, and personal API token behavior
- Refreshed third-party notices for bundled OFL fonts and `psycopg[binary]` redistribution guidance
- Added shipped OS package notice artifacts and a discoverable backend legal bundle under `/usr/share/doc/threatlens/`
- Documented trust boundaries and outbound data flows more explicitly
- Added a shipped frontend Alpine OS legal bundle and aligned the checked-in compliance artifacts with the actual built web image contents
- Improved backend machine-readable dependency metadata by preferring published `License-Expression` values over avoidable `Unknown` placeholders
- Tightened release, support, and governance language so the public repo can be used responsibly before the first immutable tag exists
