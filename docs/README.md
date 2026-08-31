# ThreatLens Documentation

This folder is a code-level reference for the current ThreatLens implementation.

Unless a page says otherwise, narrative docs use the published web-facing paths under `/api/v1`. The internal backend service also serves the same routers at `/v1`. The generated API reference is rendered from the backend schema and therefore lists `/v1/*` operation paths while also calling out the published `/api/v1` proxy base. The OpenAPI schema is intentionally published separately at `/api/openapi.json`.

## Coverage

This documentation covers:

- The published versioned API base path and deployment/runtime behavior.
- Runtime configuration values and defaults.
- Roles, token scopes, and authorization behavior.
- Access governance, handling-label policy, route attestation, and activation
  preflight behavior.
- Backend API endpoints, request/response contracts, and query parameters.
- Generated OpenAPI and release-contract artifacts.
- Database and schema field-level contracts.
- Frontend pages, UI elements, local state values, constants, storage keys, and API calls.
- AI configuration, daily briefing, enrichment, and admin operations surfaces.
- Background worker pipeline, tasks, classification/IOC extraction value sets, integration delivery, and custom tagging behavior.

## Index

- [Configuration and Deployment](./reference/configuration.md)
- [Auth, RBAC, and Token Scopes](./reference/auth-rbac.md)
- [Access Governance and Data Policy](./reference/access-governance.md)
- [Backend API Reference](./reference/api.md)
- [OpenAPI Schema](./reference/openapi.json)
- [Release Process](./reference/release-process.md)
- [Backend Runtime Inventory](./reference/backend-runtime-dependencies.txt)
- [Frontend Runtime Inventory](./reference/frontend-runtime-dependencies.txt)
- [Backend Runtime Package Metadata](./reference/backend-runtime-package-metadata.json)
- [Frontend Runtime Package Metadata](./reference/frontend-runtime-package-metadata.json)
- [Data Models and Contracts](./reference/data-models.md)
- [Frontend Reference](./reference/frontend.md)
- [Ingestion and Processing Pipeline](./reference/pipeline.md)
- [Integration Event and Delivery Platform ADR](./architecture/0001-integration-event-delivery-platform.md)
- [Bounded AI Report Generation ADR](./architecture/0002-bounded-ai-report-generation.md)
- [Operations, Investigations, Alerting V2, and IAM Hardening ADR](./architecture/0003-operations-investigations-alerting-iam.md)
- [Access Governance and Workspace Policy ADR](./architecture/0004-access-governance-and-workspace-policy.md)
- [Bundled OFL Text](./licenses/OFL-1.1.txt)
- [Bundled MIT Text](./licenses/MIT.txt)
- [Bundled BSD-2-Clause Text](./licenses/BSD-2-Clause.txt)
- [Bundled BSD-3-Clause Text](./licenses/BSD-3-Clause.txt)
- [Bundled ISC Text](./licenses/ISC.txt)
- [Bundled MPL-2.0 Text](./licenses/MPL-2.0.txt)
- [Bundled Unlicense Text](./licenses/Unlicense.txt)
- [Bundled LGPL Text](./licenses/LGPL-3.0.txt)
- [Bundled GPL Text](./licenses/GPL-3.0.txt)

## Page Guides

- [Dashboard](./pages/dashboard.md)
- [Alerts](./pages/alerts.md)
- [Investigations](./pages/investigations.md)
- [Feeds](./pages/feeds.md)
- [Export](./pages/export.md)
- [Reporting](./pages/reporting.md)
- [Stats](./pages/stats.md)
- [Settings](./pages/settings.md) including access governance, integrations,
  tagging, tokens, users, and audit logs
- [AI](./pages/ai.md)
- [PostgreSQL Backup and Recovery](./pages/operations.md)
