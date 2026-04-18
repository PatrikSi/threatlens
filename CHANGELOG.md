# Changelog

All notable changes to ThreatLens should be documented in this file.

## [Unreleased]

### Added

- Contribution, security, conduct, and changelog policy documents for public collaboration.
- Manual article fetch retry endpoint for operators.
- Durable due-feed dispatch claims to reduce duplicate polling during degraded coordination.
- Frontend helper tests covering dashboard search-state and AI run-selection behavior.

### Changed

- The default compose stack now publishes only the web service and routes API traffic through `/api`.
- The shipped environment template now uses production-oriented defaults for `APP_ENV`, secure cookies, and admin seeding.
- Settings, frontend, and configuration docs now match the current route tree and packaging model.
- Dashboard toolbar search reflects mixed per-panel search state instead of implying a stale shared search.
- RSS item expansion and note drafts are tracked per panel rather than globally.
- AI run detail selection now follows the currently visible filtered run list.

### Security

- Admin password resets now revoke existing API tokens.
- AI base URLs now use the same outbound safety controls as other fetch paths.
- Webhook request material and AI provider exchange data are retained in a safer, reduced form.

### Resilience

- Repair dispatch now retries eligible article fetch failures instead of leaving them terminal.
- Feed dispatch no longer relies solely on Redis locks for coordination.
- Queue-backed operator actions return a clear queue-unavailable contract instead of raw broker failures.
