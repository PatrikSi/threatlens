# ThreatLens Documentation

This folder is a code-level reference for the current ThreatLens implementation.

## Coverage

This documentation covers:

- Runtime configuration values and defaults.
- Roles, token scopes, and authorization behavior.
- Backend API endpoints, request/response contracts, and query parameters.
- Database and schema field-level contracts.
- Frontend pages, UI elements, local state values, constants, storage keys, and API calls.
- AI configuration, daily briefing, enrichment, and admin operations surfaces.
- Background worker pipeline, tasks, classification/IOC extraction value sets, webhook dispatch, and custom tagging behavior.

## Index

- [Configuration and Deployment](./reference/configuration.md)
- [Auth, RBAC, and Token Scopes](./reference/auth-rbac.md)
- [Backend API Reference](./reference/api.md)
- [Data Models and Contracts](./reference/data-models.md)
- [Frontend Reference](./reference/frontend.md)
- [Ingestion and Processing Pipeline](./reference/pipeline.md)

## Page Guides

- [Dashboard](./pages/dashboard.md)
- [Alerts](./pages/alerts.md)
- [Feeds](./pages/feeds.md)
- [Stats](./pages/stats.md)
- [Settings](./pages/settings.md) including notifications, tagging, tokens, users, and audit logs
- [AI](./pages/ai.md)
