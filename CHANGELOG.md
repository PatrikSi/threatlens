# Changelog

All notable changes to ThreatLens should be recorded here.

## Unreleased

### Security

- Revoked personal API tokens during admin-driven account recovery changes.
- Restricted AI base URLs with the same safe outbound controls used for feeds and articles.
- Reduced sensitive AI and webhook payload retention by storing secrets and rendered delivery payloads in encrypted form and returning redacted delivery previews.

### Reliability

- Added durable feed dispatch claims and clearer queue-unavailable behavior for operator-triggered background work.
- Added a manual article refetch path and retry handling for repairable article-fetch failures.

### UI and Docs

- Isolated dashboard note drafts per panel to avoid cross-panel state bleed.
- Updated deployment and settings documentation to match the current route structure and compose topology.
- Restored contributor, security, and conduct policy documents for open-source readiness.
